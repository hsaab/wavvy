"""Resolve-batch journeys: pagination and Beatport session failure.

These tests never hit Supabase, Beatport, Traxsource, or the network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from beatport_browser import BeatportBrowserError
import database
import link_resolver


class _DummyBrowser:
    async def close(self) -> None:
        return None


class _PagedQuery:
    """Records .range() and serves canned pages in order."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self.pages = pages
        self.range_calls: list[tuple[int, int]] = []
        self._idx = 0

    def select(self, *args: object, **kwargs: object) -> "_PagedQuery":
        return self

    def in_(self, *args: object, **kwargs: object) -> "_PagedQuery":
        return self

    def or_(self, *args: object, **kwargs: object) -> "_PagedQuery":
        return self

    def range(self, start: int, end: int) -> "_PagedQuery":
        self.range_calls.append((start, end))
        return self

    def execute(self) -> SimpleNamespace:
        if self._idx >= len(self.pages):
            return SimpleNamespace(data=[])
        data = self.pages[self._idx]
        self._idx += 1
        return SimpleNamespace(data=data)


class _FakeClient:
    def __init__(self, query: _PagedQuery) -> None:
        self._query = query

    def table(self, name: str) -> _PagedQuery:
        return self._query


def _httpx_cm() -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def test_tracks_needing_resolution_pages_past_the_postgrest_cap() -> None:
    """Auto-resolve must not stop at the silent 1000-row PostgREST default."""
    page1 = [{"id": i} for i in range(1000)]
    page2 = [{"id": 1000}]
    query = _PagedQuery([page1, page2])

    with patch.object(database, "get_supabase", return_value=_FakeClient(query)):
        rows = database.get_tracks_needing_resolution(["new", "approved", "cart_failed"])

    assert len(rows) == 1001
    assert rows[-1]["id"] == 1000
    assert query.range_calls == [(0, 999), (1000, 1999)]


def test_tracks_by_ids_uses_range_so_the_query_is_not_unbounded() -> None:
    """Explicit resolve-by-id must still page; PostgREST caps unpaginated selects."""
    ids = list(range(1, 6))
    query = _PagedQuery([[{"id": i} for i in ids]])

    with patch.object(database, "get_supabase", return_value=_FakeClient(query)):
        rows = database.get_tracks_by_ids(ids)

    assert [row["id"] for row in rows] == ids
    assert query.range_calls == [(0, 999)]


def test_resolve_track_keeps_odesli_traxsource_when_beatport_dies() -> None:
    """A Cloudflare blip must not discard an Odesli Traxsource URL already found."""
    odesli_url = "https://www.traxsource.com/track/1/bob-fossil"
    ts_search = AsyncMock(return_value=("https://www.traxsource.com/track/2/other", 90))

    async def fail_beatport(*args: object, **kwargs: object) -> tuple[None, int]:
        raise BeatportBrowserError("Just a moment")

    track = {
        "id": 1,
        "track_name": "Bob Fossil",
        "artist_name": "Gab Rhome",
        "spotify_id": "abc",
    }
    with (
        patch.object(link_resolver, "SCRAPE_DELAY_SECS", 0),
        patch.object(
            link_resolver,
            "_odesli_lookup",
            AsyncMock(return_value={"traxsource_url": odesli_url}),
        ),
        patch.object(link_resolver, "_beatport_search", fail_beatport),
        patch.object(link_resolver, "_traxsource_search", ts_search),
    ):
        result = asyncio.run(
            link_resolver.resolve_track(
                track, MagicMock(), bp_browser=MagicMock(), ts_browser=MagicMock(),
            )
        )

    assert result["traxsource_url"] == odesli_url
    assert result["match_confidence"] != "not_found"
    assert result["_beatport_session_failed"] is True
    assert any("beatport:" in err for err in result["errors"])
    ts_search.assert_not_awaited()


def test_resolve_track_still_runs_traxsource_when_beatport_dies() -> None:
    """The triggering track must get a Traxsource scrape, not abort after Beatport."""
    ts_url = "https://www.traxsource.com/track/9/bob-fossil-armen-miran-remix"

    async def fail_beatport(*args: object, **kwargs: object) -> tuple[None, int]:
        raise BeatportBrowserError("Just a moment")

    track = {
        "id": 1,
        "track_name": "Bob Fossil",
        "artist_name": "Gab Rhome",
        "spotify_id": "abc",
    }
    with (
        patch.object(link_resolver, "SCRAPE_DELAY_SECS", 0),
        patch.object(link_resolver, "_odesli_lookup", AsyncMock(return_value={})),
        patch.object(link_resolver, "_beatport_search", fail_beatport),
        patch.object(
            link_resolver, "_traxsource_search", AsyncMock(return_value=(ts_url, 88)),
        ),
    ):
        result = asyncio.run(
            link_resolver.resolve_track(
                track, MagicMock(), bp_browser=MagicMock(), ts_browser=MagicMock(),
            )
        )

    assert result["traxsource_url"] == ts_url
    assert result["confidence_score"] == 88
    assert result["_beatport_session_failed"] is True


def test_batch_persists_partial_links_then_skips_beatport_for_later_tracks() -> None:
    """The failing track is written; later tracks keep going without Beatport."""
    tracks = [
        {"id": 1, "track_name": "A", "artist_name": "X", "spotify_id": "s1"},
        {"id": 2, "track_name": "B", "artist_name": "Y", "spotify_id": "s2"},
    ]
    updates: list[tuple[int, dict]] = []
    bp_args: list[object] = []

    async def fake_resolve(
        track: dict,
        client: object,
        bp_browser: object = None,
        ts_browser: object = None,
    ) -> dict:
        bp_args.append(bp_browser)
        if track["id"] == 1:
            return {
                "beatport_url": None,
                "traxsource_url": "https://www.traxsource.com/track/1/a",
                "match_confidence": "high",
                "confidence_score": 100,
                "errors": ["beatport: Just a moment"],
                "_beatport_session_failed": True,
                "_beatport_session_error": "Just a moment",
            }
        return {
            "beatport_url": None,
            "traxsource_url": "https://www.traxsource.com/track/2/b",
            "match_confidence": "medium",
            "confidence_score": 80,
            "errors": ["beatport: browser session unavailable"],
        }

    with (
        patch.object(link_resolver, "get_tracks_by_ids", return_value=tracks),
        patch.object(
            link_resolver,
            "update_track_fields",
            lambda track_id, payload: updates.append((track_id, payload)),
        ),
        patch.object(link_resolver, "resolve_track", fake_resolve),
        patch.object(link_resolver, "BeatportBrowser", return_value=_DummyBrowser()),
        patch.object(link_resolver, "TraxsourceBrowser", return_value=_DummyBrowser()),
        patch.object(link_resolver.manager, "broadcast", AsyncMock()),
        patch.object(link_resolver.httpx, "AsyncClient", return_value=_httpx_cm()),
    ):
        summary = asyncio.run(link_resolver._run_resolve_batch([1, 2]))

    assert updates[0] == (
        1,
        {
            "match_confidence": "high",
            "confidence_score": 100,
            "traxsource_url": "https://www.traxsource.com/track/1/a",
        },
    )
    assert updates[1][0] == 2
    assert bp_args[0] is not None
    assert bp_args[1] is None
    assert summary["batch_error"]
    assert "_beatport_session_failed" not in summary["results"][0]
    assert summary["resolved"] == 2
