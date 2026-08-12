"""First-scan queue journeys for scan_playlist.

These tests never hit Spotify, Supabase, or the network. They call
scan_playlist directly with the Spotify fetch, snapshot, DB, iTunes, and
WebSocket helpers patched on the spotify_monitor module.

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

from spotify_monitor import scan_playlist

PLAYLIST_ID = "pl_house_001"
PLAYLIST_NAME = "House - Hot Since 82"


def _track(spotify_id: str, name: str, artist: str = "DJ Example") -> dict[str, Any]:
    return {
        "spotify_id": spotify_id,
        "track_name": name,
        "artist_name": artist,
        "album_name": "Example Album",
        "isrc": f"ISRC{spotify_id}",
        "spotify_url": f"https://open.spotify.com/track/{spotify_id}",
    }


class _ScanHarness:
    """In-memory stand-ins for Spotify, snapshots, DB, and iTunes."""

    def __init__(self) -> None:
        self.tracks: list[dict[str, Any]] = []
        self.snapshot: dict[str, Any] | None = None
        self.existing_ids: set[str] = set()
        self.itunes_hits: set[tuple[str, str]] = set()
        self.upserts: list[dict[str, Any]] = []
        self.saved_snapshots: list[tuple[str, str, list[str]]] = []

    def run(self, playlist_id: str = PLAYLIST_ID, playlist_name: str = PLAYLIST_NAME) -> dict[str, Any]:
        return asyncio.run(scan_playlist(playlist_id, playlist_name))

    def upserted_ids(self) -> list[str]:
        return [row["spotify_id"] for row in self.upserts]

    def upsert_for(self, spotify_id: str) -> dict[str, Any]:
        matches = [row for row in self.upserts if row["spotify_id"] == spotify_id]
        assert matches, f"expected an upsert for {spotify_id}, got {self.upserted_ids()}"
        return matches[0]


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _ScanHarness:
    h = _ScanHarness()

    def fake_fetch(_playlist_id: str) -> list[dict[str, Any]]:
        return list(h.tracks)

    def fake_snapshot(_playlist_id: str) -> dict[str, Any] | None:
        return h.snapshot

    def fake_save(playlist_id: str, playlist_name: str, track_ids: list[str]) -> None:
        h.saved_snapshots.append((playlist_id, playlist_name, list(track_ids)))

    def fake_upsert(track_data: dict[str, Any]) -> dict[str, Any]:
        copied = dict(track_data)
        h.upserts.append(copied)
        return copied

    def fake_existing(spotify_ids: list[str]) -> set[str]:
        return {sid for sid in spotify_ids if sid in h.existing_ids}

    def fake_itunes(artist_name: str, track_name: str) -> bool:
        return (artist_name, track_name) in h.itunes_hits

    monkeypatch.setattr("spotify_monitor._fetch_playlist_tracks", fake_fetch)
    monkeypatch.setattr("spotify_monitor.get_playlist_snapshot", fake_snapshot)
    monkeypatch.setattr("spotify_monitor.save_playlist_snapshot", fake_save)
    monkeypatch.setattr("spotify_monitor.upsert_track", fake_upsert)
    monkeypatch.setattr("spotify_monitor.get_existing_spotify_ids", fake_existing)
    monkeypatch.setattr("spotify_monitor._is_in_itunes_library", fake_itunes)
    monkeypatch.setattr("spotify_monitor.manager.broadcast", AsyncMock())
    monkeypatch.setattr(
        "spotify_monitor.get_config",
        lambda: {"playlist_mapping": {"House": PLAYLIST_NAME}},
    )
    return h


def test_user_adds_a_never_scanned_playlist_and_unseen_tracks_show_up_as_new_in_the_queue(
    harness: _ScanHarness,
) -> None:
    """Tracks not in the DB and not in iTunes appear as status new (Queue)."""
    harness.tracks = [
        _track("sp_fresh_1", "Night Ride"),
        _track("sp_fresh_2", "Sunrise"),
    ]

    stats = harness.run()

    assert [row["status"] for row in harness.upserts] == ["new", "new"]
    assert harness.upserted_ids() == ["sp_fresh_1", "sp_fresh_2"]
    assert stats["new"] == 2
    assert stats["baseline"] == 0
    assert stats["skipped_dup"] == 0
    assert stats["skipped_itunes"] == 0
    assert harness.saved_snapshots == [
        (PLAYLIST_ID, PLAYLIST_NAME, ["sp_fresh_1", "sp_fresh_2"]),
    ]


def test_first_scan_does_not_overwrite_a_track_that_is_already_in_the_database(
    harness: _ScanHarness,
) -> None:
    """A track already in the DB (any status) is not overwritten on first scan."""
    already_skipped = _track("sp_already_skipped", "Old Favorite")
    already_done = _track("sp_already_done", "Finished Track")
    unseen = _track("sp_unseen", "Brand New Cut")
    harness.tracks = [already_skipped, already_done, unseen]
    harness.existing_ids = {"sp_already_skipped", "sp_already_done"}

    stats = harness.run()

    assert "sp_already_skipped" not in harness.upserted_ids()
    assert "sp_already_done" not in harness.upserted_ids()
    assert harness.upsert_for("sp_unseen")["status"] == "new"
    assert stats["skipped_dup"] == 2
    assert stats["new"] == 1
    assert stats["baseline"] == 0
    assert stats["skipped_itunes"] == 0


def test_first_scan_does_not_insert_a_track_that_fuzzy_matches_itunes(
    harness: _ScanHarness,
) -> None:
    """A track that fuzzy-matches iTunes is not inserted on first scan."""
    owned = _track("sp_owned", "Library Hit", artist="Known Artist")
    unseen = _track("sp_unseen_wav", "Not On Disk")
    harness.tracks = [owned, unseen]
    harness.itunes_hits = {("Known Artist", "Library Hit")}

    stats = harness.run()

    assert "sp_owned" not in harness.upserted_ids()
    assert harness.upsert_for("sp_unseen_wav")["status"] == "new"
    assert stats["skipped_itunes"] == 1
    assert stats["new"] == 1
    assert stats["baseline"] == 0
    assert stats["skipped_dup"] == 0


def test_second_scan_of_the_same_playlist_with_no_new_spotify_tracks_inserts_nothing(
    harness: _ScanHarness,
) -> None:
    """A second scan (snapshot exists, no Spotify additions) inserts zero new rows."""
    harness.tracks = [
        _track("sp_a", "Track A"),
        _track("sp_b", "Track B"),
    ]
    harness.snapshot = {"track_ids": ["sp_a", "sp_b"]}

    stats = harness.run()

    assert harness.upserts == []
    assert stats["new"] == 0
    assert stats["baseline"] == 0
    assert stats["skipped_dup"] == 0
    assert stats["skipped_itunes"] == 0
    assert harness.saved_snapshots == [
        (PLAYLIST_ID, PLAYLIST_NAME, ["sp_a", "sp_b"]),
    ]


def test_later_scan_inserts_a_brand_new_spotify_track_as_new(
    harness: _ScanHarness,
) -> None:
    """A later scan with a brand-new Spotify id inserts it as new."""
    harness.tracks = [
        _track("sp_a", "Track A"),
        _track("sp_b", "Track B"),
        _track("sp_brand_new", "Just Added"),
    ]
    harness.snapshot = {"track_ids": ["sp_a", "sp_b"]}

    stats = harness.run()

    assert harness.upserted_ids() == ["sp_brand_new"]
    assert harness.upsert_for("sp_brand_new")["status"] == "new"
    assert stats["new"] == 1
    assert stats["baseline"] == 0
    assert stats["skipped_dup"] == 0
    assert stats["skipped_itunes"] == 0
