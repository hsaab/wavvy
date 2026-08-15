"""Map a Spotify source playlist to the iTunes set playlist it must land in."""

from __future__ import annotations

from typing import Any

from config import get_config


def itunes_set_playlist(source_playlist: str | None) -> str | None:
    """Return the iTunes playlist that belongs to *source_playlist*, or None."""
    if not source_playlist:
        return None
    mapping = get_config().get("source_playlist_mapping") or {}
    if source_playlist in mapping:
        return mapping[source_playlist] or None
    lowered = source_playlist.lower()
    for key, value in mapping.items():
        if key.lower() == lowered:
            return value or None
    return None


def playlists_for_import(track: dict[str, Any]) -> list[str]:
    """Target playlists plus the source set playlist, if one is configured.

    Extra crates the user picked stay. The source set playlist cannot be omitted,
    which is what dropped Kigelia and Alive from the set they were scanned from.
    """
    playlists = list(track.get("target_playlists") or [])
    mapped = itunes_set_playlist(track.get("source_playlist"))
    if mapped and mapped not in playlists:
        playlists.append(mapped)
    return playlists
