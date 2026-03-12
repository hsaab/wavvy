"""Supabase client setup and query helpers.

All database access goes through this module — no raw SQL in application code.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from config import get_config

logger = logging.getLogger(__name__)

_supabase: Client | None = None


def init_supabase() -> Client:
    """Initialize the Supabase client from config. Call once at startup."""
    global _supabase
    cfg = get_config()["supabase"]
    if not cfg.get("url") or not cfg.get("anon_key"):
        raise RuntimeError("Supabase URL and anon_key must be set in config.json")
    _supabase = create_client(cfg["url"], cfg["anon_key"])
    logger.info("Supabase client initialized for %s", cfg["url"])
    return _supabase


def get_supabase() -> Client:
    """Return the active Supabase client."""
    if _supabase is None:
        raise RuntimeError("Supabase not initialized — call init_supabase() first")
    return _supabase


def validate_connection() -> bool:
    """Test the Supabase connection by running a lightweight query."""
    try:
        db = get_supabase()
        db.table("tracks").select("id").limit(1).execute()
        return True
    except Exception as exc:
        logger.error("Supabase connection check failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Track helpers
# ---------------------------------------------------------------------------

def get_tracks_by_status(status: str) -> list[dict[str, Any]]:
    """Fetch all tracks matching a given status."""
    result = get_supabase().table("tracks").select("*").eq("status", status).execute()
    return result.data


def get_tracks_by_statuses(
    statuses: list[str],
    order_by: str = "date_detected",
    ascending: bool = False,
) -> list[dict[str, Any]]:
    """Fetch tracks matching any of the given statuses, sorted."""
    result = (
        get_supabase()
        .table("tracks")
        .select("*")
        .in_("status", statuses)
        .order(order_by, desc=not ascending)
        .execute()
    )
    return result.data


def get_track_by_spotify_id(spotify_id: str) -> dict[str, Any] | None:
    """Return a single track row or None."""
    result = (
        get_supabase()
        .table("tracks")
        .select("*")
        .eq("spotify_id", spotify_id)
        .execute()
    )
    return result.data[0] if result.data else None


def get_existing_spotify_ids(spotify_ids: list[str]) -> set[str]:
    """Batch-check which spotify_ids already exist in the tracks table.

    Returns the subset of IDs that have a row, regardless of status.
    Queries in chunks of 200 to stay within Supabase URL-length limits.
    """
    if not spotify_ids:
        return set()

    found: set[str] = set()
    chunk_size = 200
    for i in range(0, len(spotify_ids), chunk_size):
        chunk = spotify_ids[i : i + chunk_size]
        result = (
            get_supabase()
            .table("tracks")
            .select("spotify_id")
            .in_("spotify_id", chunk)
            .execute()
        )
        found.update(row["spotify_id"] for row in result.data)
    return found


def upsert_track(track_data: dict[str, Any]) -> dict[str, Any]:
    """Insert or update a track (keyed on spotify_id)."""
    result = (
        get_supabase()
        .table("tracks")
        .upsert(track_data, on_conflict="spotify_id")
        .execute()
    )
    return result.data[0] if result.data else {}


def update_track_status(
    track_id: int,
    status: str,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a track's status and optionally other fields."""
    payload: dict[str, Any] = {"status": status}
    if extra_fields:
        payload.update(extra_fields)
    result = (
        get_supabase()
        .table("tracks")
        .update(payload)
        .eq("id", track_id)
        .execute()
    )
    return result.data[0] if result.data else {}


def update_track_fields(track_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    """Update arbitrary fields on a track row."""
    result = (
        get_supabase()
        .table("tracks")
        .update(fields)
        .eq("id", track_id)
        .execute()
    )
    return result.data[0] if result.data else {}


def get_all_tracks(
    order_by: str = "date_detected",
    ascending: bool = False,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Fetch all tracks with sorting and an upper limit."""
    result = (
        get_supabase()
        .table("tracks")
        .select("*")
        .order(order_by, desc=not ascending)
        .limit(limit)
        .execute()
    )
    return result.data


def search_tracks(query: str) -> list[dict[str, Any]]:
    """Search tracks by name or artist (case-insensitive ilike)."""
    pattern = f"%{query}%"
    result = (
        get_supabase()
        .table("tracks")
        .select("*")
        .or_(f"track_name.ilike.{pattern},artist_name.ilike.{pattern}")
        .execute()
    )
    return result.data


def get_track_counts_by_status() -> dict[str, int]:
    """Return a mapping of status -> count for dashboard summaries."""
    result = get_supabase().table("tracks").select("status").execute()
    counts: dict[str, int] = {}
    for row in result.data:
        s = row["status"]
        counts[s] = counts.get(s, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Playlist snapshot helpers
# ---------------------------------------------------------------------------

def get_playlist_snapshot(playlist_id: str) -> dict[str, Any] | None:
    """Return the most recent snapshot for a playlist, or None."""
    result = (
        get_supabase()
        .table("playlist_snapshots")
        .select("*")
        .eq("playlist_id", playlist_id)
        .order("snapshot_date", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def save_playlist_snapshot(
    playlist_id: str,
    playlist_name: str,
    track_ids: list[str],
) -> dict[str, Any]:
    """Upsert a snapshot row for a playlist (one row per playlist_id)."""
    result = (
        get_supabase()
        .table("playlist_snapshots")
        .upsert(
            {
                "playlist_id": playlist_id,
                "playlist_name": playlist_name,
                "track_ids": track_ids,
                "snapshot_date": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="playlist_id",
        )
        .execute()
    )
    return result.data[0] if result.data else {}
