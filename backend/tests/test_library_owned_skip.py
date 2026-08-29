"""Owned-library skip journeys: buy-queue rows already in Music are skipped.

These tests never hit Spotify, Supabase, AppleScript, or the network. They call
mark_owned_queue_tracks_skipped, refresh_library_and_skip_owned, POST
/api/library/scan, and app startup with the DB helpers and library scan patched.

Install the runner (from backend/). System Python may be PEP 668 managed, so use a venv:
    python3 -m venv ../.venv
    ../.venv/bin/pip install -r requirements-dev.txt
Then:
    ../.venv/bin/pytest
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from itunes_scanner import ITunesLibraryCache
from main import app, library_scan, lifespan


def _track(
    track_id: int,
    name: str,
    artist: str,
    status: str,
) -> dict[str, Any]:
    return {
        "id": track_id,
        "spotify_id": f"sp_{track_id}",
        "track_name": name,
        "artist_name": artist,
        "status": status,
    }


class _OwnedSkipHarness:
    """In-memory stand-ins for buy-queue rows and status writes."""

    def __init__(self) -> None:
        self.tracks: list[dict[str, Any]] = []
        self.status_updates: list[tuple[int, str]] = []

    def get_tracks_by_statuses(
        self,
        statuses: list[str],
        order_by: str = "date_detected",
        ascending: bool = False,
    ) -> list[dict[str, Any]]:
        allowed = set(statuses)
        return [dict(row) for row in self.tracks if row["status"] in allowed]

    def update_tracks_status(self, track_ids: list[int], status: str) -> int:
        for track_id in track_ids:
            self.status_updates.append((track_id, status))
            for row in self.tracks:
                if row["id"] == track_id:
                    row["status"] = status
                    break
        return len(track_ids)

    def status_of(self, track_id: int) -> str:
        matches = [row["status"] for row in self.tracks if row["id"] == track_id]
        assert matches, f"expected a row for {track_id}"
        return matches[0]

    def updated_ids(self) -> list[int]:
        return [track_id for track_id, _status in self.status_updates]


@pytest.fixture
def cache() -> ITunesLibraryCache:
    return ITunesLibraryCache()


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _OwnedSkipHarness:
    h = _OwnedSkipHarness()
    monkeypatch.setattr("database.get_tracks_by_statuses", h.get_tracks_by_statuses)
    monkeypatch.setattr("database.update_tracks_status", h.update_tracks_status)
    return h


def test_buy_queue_track_whose_library_artist_is_dazed_nelav_is_marked_skipped_after_a_library_scan(
    harness: _OwnedSkipHarness,
    cache: ITunesLibraryCache,
) -> None:
    """After a library scan, a new Dazed, Nelav row already in Music is skipped."""
    from itunes_scanner import mark_owned_queue_tracks_skipped

    cache.add_entry("Dazed, Nelav", "Midnight Sun")
    harness.tracks = [
        _track(11, "Midnight Sun", "Dazed, Nelav", "new"),
    ]

    skipped = mark_owned_queue_tracks_skipped(cache)

    assert harness.status_of(11) == "skipped"
    assert harness.status_updates == [(11, "skipped")]
    assert skipped == 1


def test_buy_queue_track_not_in_the_library_stays_in_its_current_status(
    harness: _OwnedSkipHarness,
    cache: ITunesLibraryCache,
) -> None:
    """A carted row that is not in the library stays carted."""
    from itunes_scanner import mark_owned_queue_tracks_skipped

    cache.add_entry("Dazed, Nelav", "Midnight Sun")
    harness.tracks = [
        _track(22, "Brand New Cut", "Someone Else", "carted"),
    ]

    skipped = mark_owned_queue_tracks_skipped(cache)

    assert harness.status_of(22) == "carted"
    assert harness.status_updates == []
    assert skipped == 0


def test_approved_owned_track_is_not_rewritten_by_the_sweep(
    harness: _OwnedSkipHarness,
    cache: ITunesLibraryCache,
) -> None:
    """Approve and Unskip write approved, so the next sweep must leave that row alone."""
    from itunes_scanner import mark_owned_queue_tracks_skipped

    cache.add_entry("Dazed, Nelav", "Midnight Sun")
    harness.tracks = [
        _track(12, "Midnight Sun", "Dazed, Nelav", "approved"),
    ]

    skipped = mark_owned_queue_tracks_skipped(cache)

    assert harness.status_of(12) == "approved"
    assert harness.status_updates == []
    assert skipped == 0


def test_failed_scan_does_not_skip_against_the_stale_cache(
    harness: _OwnedSkipHarness,
    monkeypatch: pytest.MonkeyPatch,
    cache: ITunesLibraryCache,
) -> None:
    """A failed refresh leaves yesterday's cache in place and must not skip anyone."""
    from itunes_scanner import refresh_library_and_skip_owned

    cache.add_entry("Dazed, Nelav", "Midnight Sun")
    harness.tracks = [
        _track(13, "Midnight Sun", "Dazed, Nelav", "new"),
    ]

    def failed_scan() -> int:
        cache.scan_error = "Music app is not running"
        return 0

    monkeypatch.setattr("itunes_scanner.library_cache", cache)
    monkeypatch.setattr(cache, "scan", failed_scan)

    result = refresh_library_and_skip_owned()

    assert result == {
        "ok": False,
        "track_count": 0,
        "skipped": 0,
        "scan_error": "Music app is not running",
    }
    assert harness.status_of(13) == "new"
    assert harness.status_updates == []


def test_refresh_library_and_app_startup_both_run_the_sweep_after_the_cache_is_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh Library and startup fill the cache first, then sweep owned queue rows."""
    events: list[str] = []
    fresh = ITunesLibraryCache()
    monkeypatch.setattr("main.library_cache", fresh)
    monkeypatch.setattr("itunes_scanner.library_cache", fresh)

    def fake_scan() -> int:
        events.append("cache_filled")
        fresh.scan_error = None
        fresh.add_entry("Dazed, Nelav", "Midnight Sun")
        return 1

    def fake_sweep(cache: ITunesLibraryCache) -> int:
        events.append("sweep")
        assert cache is fresh
        assert cache.contains_fuzzy("Dazed, Nelav", "Midnight Sun")
        return 1

    monkeypatch.setattr(fresh, "scan", fake_scan)
    monkeypatch.setattr("itunes_scanner.mark_owned_queue_tracks_skipped", fake_sweep)
    monkeypatch.setattr("main.manager.broadcast", AsyncMock())
    monkeypatch.setattr("main.pipeline.start", lambda: None)
    monkeypatch.setattr("main.pipeline.stop", lambda: None)
    monkeypatch.setattr("main.init_supabase", lambda: None)
    monkeypatch.setattr("main.validate_connection", lambda: True)

    refresh = asyncio.run(library_scan())

    assert events == ["cache_filled", "sweep"]
    assert refresh == {"ok": True, "track_count": 1, "skipped": 1}

    events.clear()

    async def _startup() -> None:
        async with lifespan(app):
            pass

    asyncio.run(_startup())

    assert events == ["cache_filled", "sweep"]


def test_library_scan_returns_failure_and_does_not_broadcast_when_the_scan_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh Library with Music quit returns ok false and does not ping the UI."""
    broadcasts: list[str] = []
    fresh = ITunesLibraryCache()
    fresh.add_entry("Dazed, Nelav", "Midnight Sun")
    monkeypatch.setattr("main.library_cache", fresh)
    monkeypatch.setattr("itunes_scanner.library_cache", fresh)

    def failed_scan() -> int:
        fresh.scan_error = "Music app is not running"
        return 0

    async def record_broadcast(event_type: str, payload: object = None) -> None:
        broadcasts.append(event_type)

    monkeypatch.setattr(fresh, "scan", failed_scan)
    monkeypatch.setattr("main.manager.broadcast", record_broadcast)

    result = asyncio.run(library_scan())

    assert result["ok"] is False
    assert result["scan_error"] == "Music app is not running"
    assert result["skipped"] == 0
    assert broadcasts == []


def test_already_skipped_and_non_buy_queue_tracks_are_not_rewritten(
    harness: _OwnedSkipHarness,
    cache: ITunesLibraryCache,
) -> None:
    """Skipped, downloaded, processing, approved, and other non-buy-queue rows stay put."""
    from itunes_scanner import mark_owned_queue_tracks_skipped

    cache.add_entry("Dazed, Nelav", "Midnight Sun")
    harness.tracks = [
        _track(31, "Midnight Sun", "Dazed, Nelav", "new"),
        _track(32, "Midnight Sun", "Dazed, Nelav", "cart_failed"),
        _track(33, "Midnight Sun", "Dazed, Nelav", "skipped"),
        _track(34, "Midnight Sun", "Dazed, Nelav", "downloaded"),
        _track(35, "Midnight Sun", "Dazed, Nelav", "processing"),
        _track(36, "Midnight Sun", "Dazed, Nelav", "done"),
        _track(37, "Midnight Sun", "Dazed, Nelav", "purchased"),
        _track(38, "Midnight Sun", "Dazed, Nelav", "baseline"),
        _track(39, "Midnight Sun", "Dazed, Nelav", "approved"),
    ]

    skipped = mark_owned_queue_tracks_skipped(cache)

    assert harness.status_of(31) == "skipped"
    assert harness.status_of(32) == "skipped"
    assert harness.status_of(33) == "skipped"
    assert harness.status_of(34) == "downloaded"
    assert harness.status_of(35) == "processing"
    assert harness.status_of(36) == "done"
    assert harness.status_of(37) == "purchased"
    assert harness.status_of(38) == "baseline"
    assert harness.status_of(39) == "approved"
    assert set(harness.updated_ids()) == {31, 32}
    assert all(status == "skipped" for _track_id, status in harness.status_updates)
    assert skipped == 2


def test_buy_queue_fancy_vip_is_skipped_when_library_stored_it_as_empty_artist_filename(
    harness: _OwnedSkipHarness,
    cache: ITunesLibraryCache,
) -> None:
    """A new Fancy (VIP) is skipped when Music only has the filename-style empty-artist row."""
    from itunes_scanner import mark_owned_queue_tracks_skipped

    cache.add_entry("", "Fancy (VIP) [Extended] - Dazed, Daveartt, Tamma, Dabo")
    harness.tracks = [
        _track(41, "Fancy (VIP)", "Dazed, Daveartt, Tamma, Dabo", "new"),
    ]

    skipped = mark_owned_queue_tracks_skipped(cache)

    assert harness.status_of(41) == "skipped"
    assert harness.status_updates == [(41, "skipped")]
    assert skipped == 1


def test_buy_queue_a_solas_stays_new_when_library_only_has_asi_dazed_youtube(
    harness: _OwnedSkipHarness,
    cache: ITunesLibraryCache,
) -> None:
    """A Solas by Dazed, Nelav stays new when Music only has Así - Dazed (youtube)."""
    from itunes_scanner import mark_owned_queue_tracks_skipped

    cache.add_entry("", "Así - Dazed (youtube)")
    harness.tracks = [
        _track(42, "A Solas", "Dazed, Nelav", "new"),
    ]

    skipped = mark_owned_queue_tracks_skipped(cache)

    assert harness.status_of(42) == "new"
    assert harness.status_updates == []
    assert skipped == 0
