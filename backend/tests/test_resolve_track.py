"""Slice 4 resolve journeys: Odesli 401 degrade and Expiritualmente persist.

These tests never hit api.song.link, Beatport, Traxsource, or Playwright.
They drive resolve_tracks with httpx and store search mocked.

Install the runner (from backend/):
    /Users/hassansaab/apps/wavvy/.venv/bin/pytest tests/test_resolve_track.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from link_resolver import resolve_tracks
from test_link_resolver import EXPIRITUALMENTE_MISS_ROWS, _search_all_html

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
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_args: Any, **_kwargs: Any) -> _TracksQuery:
        return self

    def in_(self, *_args: Any, **_kwargs: Any) -> _TracksQuery:
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=list(self._rows))


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def table(self, _name: str) -> _TracksQuery:
        return _TracksQuery(self._rows)


class _FakeOdesliClient:
    """AsyncClient stand-in: every request is an Odesli 401. No network."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.requests: list[tuple[str, str]] = []

    async def __aenter__(self) -> _FakeOdesliClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def request(self, method: str, url: str, **_kwargs: Any) -> httpx.Response:
        self.requests.append((method, str(url)))
        req = httpx.Request(method, str(url))
        return httpx.Response(401, request=req)


class _ResolveHarness:
    """In-memory stand-ins for Supabase, Odesli httpx, and store browsers."""

    def __init__(self) -> None:
        self.tracks: list[dict[str, Any]] = []
        self.updates: list[tuple[int, dict[str, Any]]] = []
        self.bp_searches: list[tuple[str, str]] = []
        self.ts_searches: list[tuple[str, str]] = []
        self.bp_html = _search_all_html([])
        self.ts_html = "<html></html>"
        self.odesli = _FakeOdesliClient()

    def update_for(self, track_id: int) -> dict[str, Any]:
        matches = [fields for tid, fields in self.updates if tid == track_id]
        assert matches, f"expected persist for track {track_id}, got {self.updates}"
        return matches[0]

    def run(self, track_ids: list[int]) -> dict[str, Any]:
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
        return h.bp_html

    async def fake_ts_search(_self: object, title: str, artist: str) -> str:
        h.ts_searches.append((title, artist))
        return h.ts_html

    monkeypatch.setattr("link_resolver.get_supabase", lambda: _FakeSupabase(h.tracks))
    monkeypatch.setattr("link_resolver.update_track_fields", fake_update)
    monkeypatch.setattr("link_resolver.manager.broadcast", AsyncMock())
    monkeypatch.setattr("link_resolver.httpx.AsyncClient", lambda *a, **k: h.odesli)
    monkeypatch.setattr("link_resolver.BeatportBrowser.search", fake_bp_search)
    monkeypatch.setattr("link_resolver.TraxsourceBrowser.search", fake_ts_search)
    monkeypatch.setattr("link_resolver.SCRAPE_DELAY_SECS", 0)
    monkeypatch.setattr("link_resolver._odesli_throttle", AsyncMock())
    return h


def test_odesli_401_still_reaches_scrape_and_does_not_abort_the_batch(
    harness: _ResolveHarness,
) -> None:
    """A 401 from Odesli still scrapes both tracks; the batch keeps going."""
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

    assert harness.odesli.requests, "Odesli was never called"
    assert all("song.link" in url for _, url in harness.odesli.requests)

    scraped_titles = [title for title, _artist in harness.bp_searches]
    assert first["track_name"] in scraped_titles
    assert second["track_name"] in scraped_titles
    assert len(harness.bp_searches) == 2
    assert len(harness.ts_searches) == 2

    assert summary["total"] == 2
    assert summary.get("batch_error") in (None, "")
    assert {row["track_id"] for row in summary["results"]} == {11, 12}
    assert {tid for tid, _fields in harness.updates} == {11, 12}


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
