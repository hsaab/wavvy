"""Download-match journeys for file_pipeline.

These tests never hit the filesystem watcher, iTunes, or the network. They
score a canned Beatport-style WAV name against a carted Spotify row.

Install the runner (from backend/):
    /Users/hassansaab/apps/wavvy/.venv/bin/pytest tests/test_file_pipeline.py
"""

from __future__ import annotations

from file_pipeline import MATCH_THRESHOLD, _score_against_track

MIRACLE_DHWANGE_WAV = "Amentia - Miracle D_Hwange (Armen Miran Remix).wav"
MIRACLE_DHWANGE_TRACK = {
    "artist_name": "Amentia, Armen Miran",
    "track_name": "Miracle D'Hwange - Armen Miran Remix",
}


def test_carted_remix_wav_with_underscore_for_apostrophe_matches_the_spotify_row() -> None:
    """Beatport D_Hwange must still match Spotify D'Hwange at MATCH_THRESHOLD."""
    assert (
        _score_against_track(MIRACLE_DHWANGE_WAV, MIRACLE_DHWANGE_TRACK)
        >= MATCH_THRESHOLD
    )
