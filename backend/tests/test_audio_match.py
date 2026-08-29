"""Claim policy for Downloads matching: open queue vs sticky downloaded rows."""

from __future__ import annotations

from pathlib import Path

from audio_match import (
    MATCH_THRESHOLD,
    OPEN_MATCH_STATUSES,
    best_match,
    consume_track,
    list_audio_files,
    pick_track_for_file,
    score_against_track,
)


def _track(
    track_id: int,
    artist: str,
    title: str,
    status: str = "carted",
    download_path: str | None = None,
) -> dict:
    return {
        "id": track_id,
        "artist_name": artist,
        "track_name": title,
        "status": status,
        "download_path": download_path,
    }


def test_list_audio_files_includes_mp3_and_wav(tmp_path: Path) -> None:
    (tmp_path / "keep.wav").write_bytes(b"RIFF")
    (tmp_path / "keep.mp3").write_bytes(b"ID3")
    (tmp_path / "skip.aiff").write_bytes(b"FORM")
    (tmp_path / ".hidden.wav").write_bytes(b"RIFF")
    names = [path.name for path in list_audio_files(tmp_path)]
    assert names == ["keep.mp3", "keep.wav"]


def test_approved_is_an_open_match_status() -> None:
    assert "approved" in OPEN_MATCH_STATUSES
    assert "downloaded" not in OPEN_MATCH_STATUSES


def test_score_sibling_mixes_tie_but_existing_path_wins(tmp_path: Path) -> None:
    assigned = tmp_path / "Stereoclip - Feel The Game (Extended Remix).mp3"
    sibling = tmp_path / "Stereoclip - Feel The Game (Radio Edit).mp3"
    assigned.write_bytes(b"ID3")
    sibling.write_bytes(b"ID3")
    row = _track(
        2390,
        "Stereoclip",
        "Feel The Game - Remix",
        "downloaded",
        str(assigned.resolve()),
    )
    existing, score, kind = pick_track_for_file(assigned, [], [row])
    assert kind == "existing"
    assert existing["id"] == 2390
    assert score == 100

    stolen, _stolen_score, stolen_kind = pick_track_for_file(sibling, [], [row])
    assert stolen_kind == "none"
    assert stolen is None


def test_orphaned_downloaded_can_be_rematched(tmp_path: Path) -> None:
    gone = tmp_path / "missing.mp3"
    fresh = tmp_path / "Birds of Mind - Mi Pena (Original Mix).mp3"
    fresh.write_bytes(b"ID3")
    row = _track(1, "Birds of Mind", "Mi Pena", "downloaded", str(gone))
    track, score, kind = pick_track_for_file(fresh, [], [row])
    assert kind == "orphan"
    assert track["id"] == 1
    assert score >= MATCH_THRESHOLD


def test_consume_prevents_second_file_taking_same_open_row(tmp_path: Path) -> None:
    first = tmp_path / "Birds of Mind - Mi Pena (Original Mix).mp3"
    second = tmp_path / "Birds of Mind - Mi Pena (Radio Edit).mp3"
    first.write_bytes(b"ID3")
    second.write_bytes(b"ID3")
    open_rows = [_track(1, "Birds of Mind", "Mi Pena")]
    track, _score, kind = pick_track_for_file(first, open_rows, [])
    assert kind == "open"
    consume_track(open_rows, track["id"])
    again, _again_score, again_kind = pick_track_for_file(second, open_rows, [])
    assert again is None
    assert again_kind == "none"


def test_best_match_accepts_approved() -> None:
    track, score = best_match(
        "Stereoclip - Feel The Game (Extended Remix).mp3",
        [_track(2390, "Stereoclip", "Feel The Game - Remix", "approved")],
    )
    assert track is not None
    assert track["id"] == 2390
    assert score >= MATCH_THRESHOLD


def test_real_filename_scores() -> None:
    assert score_against_track(
        "Pheelo - I Don_t Care (Original Mix).mp3",
        _track(1, "Pheelo", "I Don’t Care"),
    ) >= MATCH_THRESHOLD
