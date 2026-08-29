"""Import journeys: process refuses a silent skip and keeps the source set.

Install the runner (from backend/):
    /Users/hassansaab/apps/wavvy/.venv/bin/pytest tests/test_file_pipeline.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from audio_match import OPEN_MATCH_STATUSES
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


def _queue_track(
    track_id: int,
    artist: str,
    title: str,
    status: str = "carted",
) -> dict:
    return {
        "id": track_id,
        "artist_name": artist,
        "track_name": title,
        "status": status,
    }


def test_should_process_accepts_wav_and_mp3(tmp_path: Path) -> None:
    pipeline = FilePipeline()
    assert pipeline._should_process(tmp_path / "a.wav") is True
    assert pipeline._should_process(tmp_path / "a.mp3") is True
    assert pipeline._should_process(tmp_path / "a.MP3") is True
    assert pipeline._should_process(tmp_path / "a.aiff") is False
    assert pipeline._should_process(tmp_path / "a.crdownload") is False


def test_scan_downloads_matches_mp3_inline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mp3 = tmp_path / "Birds of Mind - Mi Pena (Original Mix).mp3"
    mp3.write_bytes(b"ID3")
    (tmp_path / "notes.pdf").write_bytes(b"%PDF")

    requested: list[list[str]] = []

    def fake_statuses(statuses: list[str], **_kwargs) -> list[dict]:
        requested.append(list(statuses))
        return [_queue_track(2380, "Birds of Mind", "Mi Pena", "carted")]

    updates: list[tuple[int, str, dict | None]] = []

    def fake_update(track_id: int, status: str, extra: dict | None = None) -> dict:
        updates.append((track_id, status, extra))
        return {}

    monkeypatch.setattr(
        "file_pipeline.get_config",
        lambda: {"downloads_folder": str(tmp_path)},
    )
    monkeypatch.setattr("file_pipeline.SCAN_STABLE_INTERVAL", 0)
    monkeypatch.setattr("file_pipeline.get_tracks_by_statuses", fake_statuses)
    monkeypatch.setattr("file_pipeline.get_tracks_by_status", lambda _status: [])
    monkeypatch.setattr("file_pipeline.update_track_status", fake_update)

    result = FilePipeline().scan_downloads()

    assert requested == [list(OPEN_MATCH_STATUSES)]
    assert "enqueued" not in result
    assert result["count"] == 1
    assert result["files"] == [mp3.name]
    assert result["matched"][0]["track_id"] == 2380
    assert result["unmatched"] == []
    assert updates[0][0] == 2380
    assert updates[0][1] == "downloaded"
    assert updates[0][2]["download_path"] == str(mp3.resolve())


def test_scan_reports_missing_folder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "nope"
    monkeypatch.setattr(
        "file_pipeline.get_config",
        lambda: {"downloads_folder": str(missing)},
    )
    result = FilePipeline().scan_downloads()
    assert result["folder_missing"] is True
    assert result["count"] == 0


def test_scan_downloads_reports_empty_when_only_non_audio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.pdf").write_bytes(b"%PDF")
    called = {"db": False}
    monkeypatch.setattr(
        "file_pipeline.get_config",
        lambda: {"downloads_folder": str(tmp_path)},
    )
    monkeypatch.setattr(
        "file_pipeline.get_tracks_by_statuses",
        lambda *_a, **_k: called.__setitem__("db", True) or [],
    )

    result = FilePipeline().scan_downloads()

    assert called["db"] is False
    assert result == {
        "count": 0,
        "matched": [],
        "unmatched": [],
        "files": [],
        "folder_missing": False,
    }


def test_scan_skips_zero_byte_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    empty = tmp_path / "Birds of Mind - Mi Pena (Original Mix).mp3"
    empty.write_bytes(b"")
    monkeypatch.setattr(
        "file_pipeline.get_config",
        lambda: {"downloads_folder": str(tmp_path)},
    )
    monkeypatch.setattr("file_pipeline.SCAN_STABLE_INTERVAL", 0)
    monkeypatch.setattr(
        "file_pipeline.get_tracks_by_statuses",
        lambda *_a, **_k: [_queue_track(1, "Birds of Mind", "Mi Pena")],
    )
    monkeypatch.setattr("file_pipeline.get_tracks_by_status", lambda _status: [])
    updates: list = []
    monkeypatch.setattr(
        "file_pipeline.update_track_status",
        lambda *a, **k: updates.append(a),
    )

    result = FilePipeline().scan_downloads()

    assert result["matched"] == []
    assert result["unmatched"][0]["reason"] == "empty"
    assert updates == []


def test_scan_second_mix_does_not_claim_same_track(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "Birds of Mind - Mi Pena (Original Mix).mp3"
    second = tmp_path / "Birds of Mind - Mi Pena (Radio Edit).mp3"
    first.write_bytes(b"ID3")
    second.write_bytes(b"ID3")
    monkeypatch.setattr(
        "file_pipeline.get_config",
        lambda: {"downloads_folder": str(tmp_path)},
    )
    monkeypatch.setattr("file_pipeline.SCAN_STABLE_INTERVAL", 0)
    monkeypatch.setattr(
        "file_pipeline.get_tracks_by_statuses",
        lambda *_a, **_k: [_queue_track(1, "Birds of Mind", "Mi Pena")],
    )
    monkeypatch.setattr("file_pipeline.get_tracks_by_status", lambda _status: [])
    updates: list[int] = []
    monkeypatch.setattr(
        "file_pipeline.update_track_status",
        lambda track_id, status, extra=None: updates.append(track_id) or {},
    )

    result = FilePipeline().scan_downloads()

    assert [row["track_id"] for row in result["matched"]] == [1]
    assert len(result["unmatched"]) == 1
    assert updates == [1]


def test_scan_does_not_overwrite_existing_download_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assigned = tmp_path / "Stereoclip - Feel The Game (Extended Remix).mp3"
    sibling = tmp_path / "Stereoclip - Feel The Game (Radio Edit).mp3"
    assigned.write_bytes(b"ID3")
    sibling.write_bytes(b"ID3")
    downloaded = [_queue_track(2390, "Stereoclip", "Feel The Game - Remix", "downloaded")]
    downloaded[0]["download_path"] = str(assigned.resolve())
    monkeypatch.setattr(
        "file_pipeline.get_config",
        lambda: {"downloads_folder": str(tmp_path)},
    )
    monkeypatch.setattr("file_pipeline.SCAN_STABLE_INTERVAL", 0)
    monkeypatch.setattr("file_pipeline.get_tracks_by_statuses", lambda *_a, **_k: [])
    monkeypatch.setattr("file_pipeline.get_tracks_by_status", lambda _status: downloaded)
    updates: list = []
    monkeypatch.setattr(
        "file_pipeline.update_track_status",
        lambda *a, **k: updates.append(a),
    )

    result = FilePipeline().scan_downloads()

    assert result["matched"][0]["track_id"] == 2390
    assert result["matched"][0]["kind"] == "existing"
    assert any(row["filename"] == sibling.name for row in result["unmatched"])
    assert updates == []
