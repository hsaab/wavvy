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
