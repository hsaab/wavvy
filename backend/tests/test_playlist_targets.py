"""Journeys: a scanned track always keeps its source set playlist.

Install the runner (from backend/):
    /Users/hassansaab/apps/wavvy/.venv/bin/pytest tests/test_playlist_targets.py
"""

from __future__ import annotations

from playlist_targets import itunes_set_playlist, playlists_for_import


def test_latin_tech_house_always_maps_to_the_latin_set(monkeypatch) -> None:
    """A latin tech house scan must land in S - Latin/Tribal House."""
    monkeypatch.setattr(
        "playlist_targets.get_config",
        lambda: {
            "source_playlist_mapping": {
                "latin tech house": "S - Latin/Tribal House",
            },
        },
    )

    assert itunes_set_playlist("latin tech house") == "S - Latin/Tribal House"
    assert itunes_set_playlist("Latin Tech House") == "S - Latin/Tribal House"


def test_user_picked_melodic_crates_still_keeps_the_source_set(monkeypatch) -> None:
    """Alive-style picks (Melodic/Tech) still include the latin set playlist."""
    monkeypatch.setattr(
        "playlist_targets.get_config",
        lambda: {
            "source_playlist_mapping": {
                "latin tech house": "S - Latin/Tribal House",
            },
        },
    )
    track = {
        "source_playlist": "latin tech house",
        "target_playlists": [
            "00 - 1A - All (city Vineyard)",
            "S - Melodic House",
            "S - Tech House",
        ],
    }

    playlists = playlists_for_import(track)

    assert playlists == [
        "00 - 1A - All (city Vineyard)",
        "S - Melodic House",
        "S - Tech House",
        "S - Latin/Tribal House",
    ]


def test_kigelia_style_latin_picks_still_keep_downtempo(monkeypatch) -> None:
    """A downtempo scan that was tagged Latin still keeps S - Downtempo Trance."""
    monkeypatch.setattr(
        "playlist_targets.get_config",
        lambda: {
            "source_playlist_mapping": {
                "downtempo trance": "S - Downtempo Trance",
            },
        },
    )
    track = {
        "source_playlist": "downtempo trance",
        "target_playlists": ["S - Latin/Tribal House", "00 - 1A - All (city Vineyard)"],
    }

    assert "S - Downtempo Trance" in playlists_for_import(track)


def test_unmapped_source_playlist_does_not_invent_a_set(monkeypatch) -> None:
    """intothejungle has no single set crate, so extra playlists stay as picked."""
    monkeypatch.setattr(
        "playlist_targets.get_config",
        lambda: {"source_playlist_mapping": {}},
    )
    track = {
        "source_playlist": "intothejungle",
        "target_playlists": ["S - Afrohouse"],
    }

    assert playlists_for_import(track) == ["S - Afrohouse"]
    assert itunes_set_playlist("intothejungle") is None
