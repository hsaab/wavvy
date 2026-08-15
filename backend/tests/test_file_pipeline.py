"""Import journeys: process refuses a silent skip and keeps the source set.

Install the runner (from backend/):
    /Users/hassansaab/apps/wavvy/.venv/bin/pytest tests/test_file_pipeline.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from file_pipeline import FilePipeline


def test_import_adds_the_source_set_even_when_the_user_omitted_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Kigelia-style import still duplicates into S - Downtempo Trance."""
    added: list[list[str]] = []

    monkeypatch.setattr("file_pipeline.is_music_app_running", lambda: True)
    monkeypatch.setattr(
        "file_pipeline.add_to_multiple_playlists",
        lambda _path, playlists: added.append(list(playlists)),
    )
    monkeypatch.setattr(
        "playlist_targets.get_config",
        lambda: {
            "source_playlist_mapping": {
                "downtempo trance": "S - Downtempo Trance",
            },
        },
    )

    wav = tmp_path / "Elfenberg - Kigelia (Original Mix).wav"
    wav.write_bytes(b"RIFF")
    FilePipeline._import_to_itunes(
        wav,
        {
            "source_playlist": "downtempo trance",
            "target_playlists": ["S - Latin/Tribal House"],
            "artist_name": "Elfenberg",
            "track_name": "Kigelia",
        },
    )

    assert added == [["S - Latin/Tribal House", "S - Downtempo Trance"]]


def test_import_raises_when_music_is_not_running_instead_of_marking_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A process with Music closed must fail, not skip the set playlist."""
    monkeypatch.setattr("file_pipeline.is_music_app_running", lambda: False)
    add = MagicMock()
    monkeypatch.setattr("file_pipeline.add_to_multiple_playlists", add)

    wav = tmp_path / "track.wav"
    wav.write_bytes(b"RIFF")

    with pytest.raises(RuntimeError, match="Apple Music is not running"):
        FilePipeline._import_to_itunes(
            wav,
            {
                "source_playlist": "latin tech house",
                "target_playlists": ["S - Latin/Tribal House"],
            },
        )

    add.assert_not_called()


def test_process_track_keeps_drive_path_when_music_is_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Music-closed failure after the move must still point at the WAV on the drive."""
    downloads = tmp_path / "Downloads"
    drive = tmp_path / "Drive"
    downloads.mkdir()
    drive.mkdir()
    wav = downloads / "Artist - Track.wav"
    wav.write_bytes(b"RIFF")

    track: dict = {
        "id": 42,
        "status": "downloaded",
        "download_path": str(wav),
        "track_name": "Track",
        "artist_name": "Artist",
        "source_playlist": "latin tech house",
        "target_playlists": ["S - Latin/Tribal House"],
        "genre": "House",
    }
    status_updates: list[tuple] = []

    def fake_update(track_id: int, status: str, extra: dict | None = None) -> None:
        status_updates.append((track_id, status, extra))
        if extra:
            track.update(extra)
        track["status"] = status

    class _Result:
        def __init__(self, data: list) -> None:
            self.data = data

    class _Query:
        def select(self, *_args: object, **_kwargs: object) -> "_Query":
            return self

        def eq(self, *_args: object, **_kwargs: object) -> "_Query":
            return self

        def execute(self) -> _Result:
            return _Result([dict(track)])

    class _Client:
        def table(self, _name: str) -> _Query:
            return _Query()

    monkeypatch.setattr("database.get_supabase", lambda: _Client())
    monkeypatch.setattr("file_pipeline.update_track_status", fake_update)
    monkeypatch.setattr(
        "file_pipeline.get_config",
        lambda: {"external_drive_path": str(drive)},
    )
    monkeypatch.setattr(
        "file_pipeline.playlists_for_import",
        lambda _track: ["S - Latin/Tribal House"],
    )
    monkeypatch.setattr("file_pipeline.is_music_app_running", lambda: False)
    monkeypatch.setattr("file_pipeline.notify_file_processed", lambda *_a, **_k: None)
    monkeypatch.setattr("file_pipeline.library_cache.add_entry", lambda **_k: None)
    add = MagicMock()
    monkeypatch.setattr("file_pipeline.add_to_multiple_playlists", add)

    pipeline = FilePipeline()
    with pytest.raises(RuntimeError, match="Apple Music is not running"):
        pipeline.process_track(42)

    dest = drive / wav.name
    assert dest.exists()
    assert not wav.exists()
    assert status_updates[-1] == (
        42,
        "downloaded",
        {"download_path": str(dest.resolve())},
    )
    add.assert_not_called()

    monkeypatch.setattr("file_pipeline.is_music_app_running", lambda: True)
    result = pipeline.process_track(42)

    assert result == {"ok": True, "destination": str(dest)}
    assert dest.exists()
    assert not (drive / f"{wav.stem} (1){wav.suffix}").exists()
    add.assert_called_once()
    assert add.call_args[0][0] == dest
    assert add.call_args[0][1] == ["S - Latin/Tribal House"]
    assert status_updates[-1][1] == "done"
