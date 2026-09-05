"""Resolve journeys: Beatport-only scrape, persist hits, keep the batch going.

These tests never hit api.song.link, Beatport, Traxsource, or Playwright.
They drive resolve_tracks with httpx and store search mocked.

Install the runner (from backend/):
    ../.venv/bin/pytest tests/test_resolve_track.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

import link_resolver as link_resolver_mod
from beatport_browser import BeatportBrowserError
from link_resolver import RESOLVABLE_STATUSES, resolve_tracks
from test_link_resolver import (
    BEATPORT_ELECTRIC_LOVE_REMIX,
    ELECTRIC_LOVE_ARTISTS,
    ELECTRIC_LOVE_ROW,
    ELECTRIC_LOVE_TITLE,
    EXPIRITUALMENTE_MISS_ROWS,
    _search_all_html,
)

EXPIRITUALMENTE_ARTISTS = "Sebastian Ledher, Jambene"
EXPIRITUALMENTE_TITLE = "Expiritualmente"
EXPIRITUALMENTE_ISRC = "USEXP0000000"


def _track(
    track_id: int,
    *,
    name: str,
    artist: str,
    spotify_id: str,
    isrc: str | None = None,
) -> dict[str, Any]:
    return {
        "id": track_id,
        "track_name": name,
        "artist_name": artist,
        "spotify_id": spotify_id,
        "isrc": isrc,
        "status": "new",
    }


class _TracksQuery:
    """Records PostgREST filters so tests can inspect the unscoped fetch."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.filters: list[tuple[Any, ...]] = []

    def select(self, *_args: Any, **_kwargs: Any) -> _TracksQuery:
        return self

    def in_(self, column: str, values: Any, **_kwargs: Any) -> _TracksQuery:
        self.filters.append(("in_", column, list(values)))
        return self

    def or_(self, expression: str, **_kwargs: Any) -> _TracksQuery:
        self.filters.append(("or_", expression))
        return self

    def is_(self, column: str, value: Any, **_kwargs: Any) -> _TracksQuery:
        self.filters.append(("is_", column, value))
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=list(self._rows))

    def filter_blob(self) -> str:
        """Flatten recorded filters for substring checks."""
        chunks: list[str] = []
        for item in self.filters:
            chunks.extend(str(part) for part in item)
        return " ".join(chunks)


class _FakeSupabase:
    def __init__(self, harness: _ResolveHarness) -> None:
        self._harness = harness

    def table(self, _name: str) -> _TracksQuery:
        query = _TracksQuery(self._harness.tracks)
        self._harness.last_query = query
        return query


class _RecordingHttpxClient:
    """AsyncClient stand-in that records URLs and never touches the network."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.requests: list[tuple[str, str]] = []

    async def __aenter__(self) -> _RecordingHttpxClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def request(self, method: str, url: str, **_kwargs: Any) -> httpx.Response:
        self.requests.append((method, str(url)))
        req = httpx.Request(method, str(url))
        return httpx.Response(401, request=req)


class _ResolveHarness:
    """In-memory stand-ins for Supabase, httpx, and store browsers."""

    def __init__(self) -> None:
        self.tracks: list[dict[str, Any]] = []
        self.updates: list[tuple[int, dict[str, Any]]] = []
        self.bp_searches: list[tuple[str, str]] = []
        self.ts_searches: list[tuple[str, str]] = []
        self.bp_html = _search_all_html([])
        self.ts_html = "<html></html>"
        self.httpx_client = _RecordingHttpxClient()
        self.bp_search_error: Exception | None = None
        self.last_query: _TracksQuery | None = None

    def update_for(self, track_id: int) -> dict[str, Any]:
        matches = [fields for tid, fields in self.updates if tid == track_id]
        assert matches, f"expected persist for track {track_id}, got {self.updates}"
        return matches[0]

    def song_link_urls(self) -> list[str]:
        """URLs that went to song.link, if any."""
        return [url for _, url in self.httpx_client.requests if "song.link" in url]

    def run(self, track_ids: list[int] | None) -> dict[str, Any]:
        return asyncio.run(resolve_tracks(track_ids))


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _ResolveHarness:
    h = _ResolveHarness()

    def fake_update(track_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        copied = dict(fields)
        h.updates.append((track_id, copied))
        return copied

    async def fake_bp_search(_self: object, title: str, artist: str) -> str:
        h.bp_searches.append((title, artist))
        if h.bp_search_error is not None:
            raise h.bp_search_error
        return h.bp_html

    monkeypatch.setattr("link_resolver.get_supabase", lambda: _FakeSupabase(h))
    monkeypatch.setattr("link_resolver.update_track_fields", fake_update)
    monkeypatch.setattr("link_resolver.manager.broadcast", AsyncMock())
    # Patch the httpx module so a leftover Odesli client is still recorded,
    # and so the fixture does not require link_resolver to keep importing httpx.
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: h.httpx_client)
    monkeypatch.setattr("link_resolver.BeatportBrowser.search", fake_bp_search)
    monkeypatch.setattr("link_resolver.SCRAPE_DELAY_SECS", 0)
    if hasattr(link_resolver_mod, "_odesli_throttle"):
        monkeypatch.setattr(link_resolver_mod, "_odesli_throttle", AsyncMock())
    return h


def test_resolve_links_does_not_request_song_link_and_still_scrapes_the_batch(
    harness: _ResolveHarness,
) -> None:
    """Resolve Links scrapes Beatport only and does not ask song.link."""
    first = _track(
        11,
        name="Electric Love - Yulia Niko Remix",
        artist="Aiwaska, Starving Yet Full, Yulia Niko",
        spotify_id="sp_electric_love",
        isrc="DEA002412345",
    )
    second = _track(
        12,
        name="Echo",
        artist="Holed Coin",
        spotify_id="sp_echo",
        isrc="GBECHO0000001",
    )
    harness.tracks = [first, second]

    summary = harness.run([11, 12])

    song_link_urls = harness.song_link_urls()
    assert song_link_urls == [], (
        "Resolve Links must not request song.link; "
        f"got {song_link_urls}"
    )

    scraped_titles = [title for title, _artist in harness.bp_searches]
    assert first["track_name"] in scraped_titles
    assert second["track_name"] in scraped_titles
    assert len(harness.bp_searches) == 2
    assert harness.ts_searches == [], (
        "Resolve Links must not search Traxsource; "
        f"got {harness.ts_searches}"
    )

    assert summary["total"] == 2
    assert summary.get("batch_error") in (None, "")
    assert {row["track_id"] for row in summary["results"]} == {11, 12}
    assert {tid for tid, _fields in harness.updates} == {11, 12}


def test_scrape_found_electric_love_beatport_url_is_persisted(
    harness: _ResolveHarness,
) -> None:
    """A Beatport hit for Electric Love (Yulia Niko Remix) is written to the track."""
    track = _track(
        31,
        name=ELECTRIC_LOVE_TITLE,
        artist=ELECTRIC_LOVE_ARTISTS,
        spotify_id="sp_electric_love",
        isrc=ELECTRIC_LOVE_ROW["isrc"],
    )
    harness.tracks = [track]
    harness.bp_html = _search_all_html([ELECTRIC_LOVE_ROW])

    summary = harness.run([31])

    persisted = harness.update_for(31)
    assert persisted["beatport_url"] == BEATPORT_ELECTRIC_LOVE_REMIX
    assert persisted["match_confidence"] != "not_found"

    result = summary["results"][0]
    assert result["beatport_url"] == BEATPORT_ELECTRIC_LOVE_REMIX
    assert summary.get("batch_error") in (None, "")


def test_expiritualmente_style_no_hit_persists_not_found_with_no_beatport_url(
    harness: _ResolveHarness,
) -> None:
    """Unrelated Ledher rows stay not_found; persist must not keep a Beatport URL."""
    track = _track(
        21,
        name=EXPIRITUALMENTE_TITLE,
        artist=EXPIRITUALMENTE_ARTISTS,
        spotify_id="sp_expiritualmente",
        isrc=EXPIRITUALMENTE_ISRC,
    )
    harness.tracks = [track]
    harness.bp_html = _search_all_html(EXPIRITUALMENTE_MISS_ROWS)

    summary = harness.run([21])

    assert harness.bp_searches == [
        (EXPIRITUALMENTE_TITLE, EXPIRITUALMENTE_ARTISTS),
    ]

    persisted = harness.update_for(21)
    assert persisted["match_confidence"] == "not_found"
    assert "beatport_url" not in persisted
    assert "traxsource_url" not in persisted

    result = summary["results"][0]
    assert result["match_confidence"] == "not_found"
    assert result.get("beatport_url") in (None, "")
    assert summary.get("batch_error") in (None, "")


def test_batch_still_continues_when_beatport_search_fails(
    harness: _ResolveHarness,
) -> None:
    """A dead Beatport session does not stop the next track from resolving."""
    first = _track(
        41,
        name="Electric Love - Yulia Niko Remix",
        artist="Aiwaska, Starving Yet Full, Yulia Niko",
        spotify_id="sp_electric_love",
        isrc="DEA002412345",
    )
    second = _track(
        42,
        name="Echo",
        artist="Holed Coin",
        spotify_id="sp_echo",
        isrc="GBECHO0000001",
    )
    harness.tracks = [first, second]
    harness.bp_search_error = BeatportBrowserError("Failed to launch Chromium")

    summary = harness.run([41, 42])

    assert {row["track_id"] for row in summary["results"]} == {41, 42}
    assert summary.get("batch_error") not in (None, "")

    persisted = harness.update_for(42)
    assert persisted["match_confidence"]


def test_unscoped_resolve_does_not_select_tracks_that_only_lack_traxsource_url(
    harness: _ResolveHarness,
) -> None:
    """Resolve-all queues tracks missing Beatport, not Traxsource-only gaps."""
    harness.tracks = []

    harness.run(None)

    query = harness.last_query
    assert query is not None

    status_filters = [
        item[2]
        for item in query.filters
        if item[0] == "in_" and item[1] == "status"
    ]
    assert status_filters, "unscoped fetch must keep the resolvable-status filter"
    assert set(status_filters[0]) == set(RESOLVABLE_STATUSES)

    blob = query.filter_blob()
    assert "traxsource_url.is.null" not in blob, (
        "unscoped fetch must not OR traxsource_url.is.null; "
        f"got {query.filters}"
    )
    assert "traxsource_url" not in blob, (
        "unscoped fetch must not filter on traxsource_url; "
        f"got {query.filters}"
    )

    beatport_null = (
        "beatport_url.is.null" in blob
        or any(
            item[0] == "is_" and item[1] == "beatport_url"
            for item in query.filters
        )
    )
    assert beatport_null, (
        "unscoped fetch must still require beatport_url null; "
        f"got {query.filters}"
    )


def test_link_resolver_does_not_expose_traxsource_search_helpers() -> None:
    """Dead Traxsource resolve helpers must be gone so they cannot come back."""
    assert not hasattr(link_resolver_mod, "TraxsourceBrowser"), (
        "link_resolver must not expose TraxsourceBrowser"
    )
    assert not hasattr(link_resolver_mod, "_traxsource_search"), (
        "link_resolver must not expose _traxsource_search"
    )
