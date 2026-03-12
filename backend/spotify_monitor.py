"""Spotify playlist monitoring with OAuth, baseline/dedup scan, and WebSocket events.

Handles:
- Spotipy OAuth2 with token caching
- Fetching the current user's playlists
- Scanning playlists for new tracks (baseline on first run, diff on subsequent)
- 5-check dedup before inserting a track as 'new'
- Snapshot persistence to Supabase
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from config import get_config
from database import (
    get_existing_spotify_ids,
    get_playlist_snapshot,
    save_playlist_snapshot,
    upsert_track,
)
from itunes_scanner import library_cache
from notifications import notify_scan_complete
from ws_manager import manager

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).parent.parent / ".spotify_cache"

_spotify_client: spotipy.Spotify | None = None


def _build_oauth() -> SpotifyOAuth:
    """Create a SpotifyOAuth manager from the current config."""
    cfg = get_config()["spotify"]
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        raise RuntimeError("Spotify client_id and client_secret must be set in config.json")

    return SpotifyOAuth(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        redirect_uri=cfg.get("redirect_uri", "http://127.0.0.1:8888/callback"),
        scope=(
            "playlist-read-private playlist-read-collaborative "
            "streaming user-read-email user-read-private "
            "user-read-playback-state user-modify-playback-state"
        ),
        cache_path=str(CACHE_PATH),
        open_browser=False,
    )


def get_spotify() -> spotipy.Spotify:
    """Return an authenticated Spotipy client, refreshing the token if needed."""
    global _spotify_client
    auth_manager = _build_oauth()

    token_info = auth_manager.cache_handler.get_cached_token()
    if not token_info:
        raise RuntimeError(
            "No cached Spotify token. Complete OAuth flow first via /api/spotify/auth-url"
        )

    if auth_manager.is_token_expired(token_info):
        token_info = auth_manager.refresh_access_token(token_info["refresh_token"])

    _spotify_client = spotipy.Spotify(auth=token_info["access_token"])
    return _spotify_client


def get_access_token() -> str:
    """Return a valid Spotify access token, refreshing if expired.

    Raises RuntimeError if the cached token is missing required scopes
    so the caller can prompt re-authorization.
    """
    auth_manager = _build_oauth()
    token_info = auth_manager.cache_handler.get_cached_token()
    if not token_info:
        raise RuntimeError(
            "No cached Spotify token. Complete OAuth flow first via /api/spotify/auth-url"
        )

    cached_scopes = set(token_info.get("scope", "").split())
    missing = REQUIRED_SCOPES - cached_scopes
    if missing:
        logger.warning("Cached token missing scopes: %s — deleting cache to force re-auth", missing)
        CACHE_PATH.unlink(missing_ok=True)
        raise RuntimeError(
            f"Token missing required scopes ({', '.join(sorted(missing))}). "
            "Please reconnect Spotify via /api/spotify/auth-url"
        )

    if auth_manager.is_token_expired(token_info):
        token_info = auth_manager.refresh_access_token(token_info["refresh_token"])
    return token_info["access_token"]


def get_auth_url() -> str:
    """Return the Spotify authorization URL for the user to grant access."""
    return _build_oauth().get_authorize_url()


def exchange_code(code: str) -> dict[str, Any]:
    """Exchange an authorization code for access + refresh tokens, then cache them."""
    auth_manager = _build_oauth()
    token_info = auth_manager.get_access_token(code, as_dict=True)
    return token_info


REQUIRED_SCOPES = {
    "playlist-read-private", "playlist-read-collaborative",
    "streaming", "user-read-email", "user-read-private",
    "user-read-playback-state", "user-modify-playback-state",
}


def is_authenticated() -> bool:
    """Check whether a valid (possibly expired but refreshable) token is cached."""
    try:
        auth_manager = _build_oauth()
        token_info = auth_manager.cache_handler.get_cached_token()
        return token_info is not None
    except RuntimeError:
        return False


def has_streaming_scope() -> bool:
    """Check whether the cached token includes the streaming scope."""
    try:
        auth_manager = _build_oauth()
        token_info = auth_manager.cache_handler.get_cached_token()
        if not token_info:
            return False
        cached_scopes = set(token_info.get("scope", "").split())
        return REQUIRED_SCOPES.issubset(cached_scopes)
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# Playlist fetching
# ---------------------------------------------------------------------------

def fetch_user_playlists() -> list[dict[str, Any]]:
    """Return a trimmed list of the current user's playlists."""
    sp = get_spotify()
    playlists: list[dict[str, Any]] = []
    results = sp.current_user_playlists(limit=50)

    while results:
        for item in results["items"]:
            playlists.append({
                "id": item["id"],
                "name": item["name"],
                "track_count": item["tracks"]["total"],
                "image": item["images"][0]["url"] if item.get("images") else None,
            })
        results = sp.next(results) if results.get("next") else None

    return playlists


def _fetch_playlist_tracks(playlist_id: str) -> list[dict[str, Any]]:
    """Fetch every track from a Spotify playlist, handling pagination."""
    sp = get_spotify()
    tracks: list[dict[str, Any]] = []
    results = sp.playlist_tracks(
        playlist_id,
        fields="items(track(id,name,artists,album,external_ids,external_urls)),next",
    )

    while results:
        for item in results["items"]:
            track = item.get("track")
            if not track or not track.get("id"):
                continue
            tracks.append({
                "spotify_id": track["id"],
                "track_name": track["name"],
                "artist_name": ", ".join(a["name"] for a in track.get("artists", [])),
                "album_name": track.get("album", {}).get("name", ""),
                "isrc": track.get("external_ids", {}).get("isrc", ""),
                "spotify_url": track.get("external_urls", {}).get("spotify", ""),
            })
        results = sp.next(results) if results.get("next") else None

    return tracks


# ---------------------------------------------------------------------------
# Scan logic
# ---------------------------------------------------------------------------

def _detect_genre_for_playlist(playlist_name: str) -> str | None:
    """Auto-detect genre based on playlist_mapping in config."""
    mapping = get_config().get("playlist_mapping", {})
    for genre, mapped_name in mapping.items():
        if mapped_name.lower() == playlist_name.lower():
            return genre
    return None


def _is_in_itunes_library(artist_name: str, track_name: str) -> bool:
    """Check whether a track exists in the local Apple Music library (fuzzy match)."""
    if not artist_name or not track_name or library_cache.track_count == 0:
        return False
    return library_cache.contains_fuzzy(artist_name, track_name)


async def scan_playlist(playlist_id: str, playlist_name: str) -> dict[str, Any]:
    """Scan a single playlist: baseline on first run, diff on subsequent runs.

    Returns a summary dict with counts of new, baseline, skipped, and errored tracks.
    """
    await manager.broadcast("scan_started", {
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
    })

    playlist_tracks = await asyncio.to_thread(_fetch_playlist_tracks, playlist_id)
    current_track_ids = [t["spotify_id"] for t in playlist_tracks]
    track_lookup = {t["spotify_id"]: t for t in playlist_tracks}

    snapshot = await asyncio.to_thread(get_playlist_snapshot, playlist_id)
    is_baseline = snapshot is None

    # #region agent log
    import json as _json, time as _time
    _log_path = Path(__file__).parent.parent / ".cursor" / "debug-5d0c12.log"
    with open(_log_path, "a") as _f:
        _f.write(_json.dumps({"sessionId":"5d0c12","location":"spotify_monitor.py:scan_playlist","message":"Playlist scan starting","data":{"playlist_name":playlist_name,"is_baseline":is_baseline,"track_count":len(playlist_tracks),"snapshot_exists":snapshot is not None},"timestamp":int(_time.time()*1000),"hypothesisId":"H3"}) + "\n")
    # #endregion

    genre = _detect_genre_for_playlist(playlist_name)
    stats: dict[str, Any] = {
        "playlist": playlist_name,
        "baseline": 0,
        "new": 0,
        "skipped_dup": 0,
        "skipped_itunes": 0,
        "errors": 0,
        "total": len(playlist_tracks),
    }

    if is_baseline:
        for idx, track_data in enumerate(playlist_tracks):
            try:
                await asyncio.to_thread(
                    upsert_track,
                    {
                        **track_data,
                        "status": "baseline",
                        "source_playlist": playlist_name,
                        "genre": genre,
                    },
                )
                stats["baseline"] += 1
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "Failed to upsert baseline track '%s': %s",
                    track_data.get("track_name", "?"), exc,
                )

            if (idx + 1) % 10 == 0:
                await manager.broadcast("scan_progress", {
                    "playlist": playlist_name,
                    "processed": idx + 1,
                    "total": stats["total"],
                    "phase": "baseline",
                })

        logger.info(
            "Baseline scan for '%s': %d stored, %d errors",
            playlist_name, stats["baseline"], stats["errors"],
        )

    else:
        previous_ids = set(snapshot.get("track_ids", []))
        new_ids = [tid for tid in current_track_ids if tid not in previous_ids]

        if new_ids:
            # Batch DB lookup: one query instead of N individual lookups
            already_in_db = await asyncio.to_thread(get_existing_spotify_ids, new_ids)
        else:
            already_in_db = set()

        for idx, spotify_id in enumerate(new_ids):
            track_data = track_lookup[spotify_id]
            artist = track_data.get("artist_name", "")
            title = track_data.get("track_name", "")

            # Checks 1-4: track already exists in Supabase (any status)
            if spotify_id in already_in_db:
                stats["skipped_dup"] += 1
                continue

            # Check 5: fuzzy match against Apple Music library
            itunes_hit = await asyncio.to_thread(_is_in_itunes_library, artist, title)
            if itunes_hit:
                stats["skipped_itunes"] += 1
                logger.debug("iTunes dedup hit: '%s - %s'", artist, title)
                continue

            try:
                await asyncio.to_thread(
                    upsert_track,
                    {
                        **track_data,
                        "status": "new",
                        "source_playlist": playlist_name,
                        "genre": genre,
                    },
                )
                stats["new"] += 1
            except Exception as exc:
                stats["errors"] += 1
                logger.error("Failed to upsert new track '%s': %s", title, exc)

            if (idx + 1) % 5 == 0:
                await manager.broadcast("scan_progress", {
                    "playlist": playlist_name,
                    "processed": idx + 1,
                    "total": len(new_ids),
                    "phase": "diff",
                })

        logger.info(
            "Diff scan for '%s': %d new, %d db-dups, %d iTunes-dups, %d errors (of %d unseen)",
            playlist_name, stats["new"], stats["skipped_dup"],
            stats["skipped_itunes"], stats["errors"], len(new_ids),
        )

    # Save updated snapshot (even if some tracks errored — snapshot reflects Spotify state)
    await asyncio.to_thread(
        save_playlist_snapshot, playlist_id, playlist_name, current_track_ids,
    )

    await manager.broadcast("scan_complete", stats)
    return stats


async def scan_monitored_playlists(
    playlist_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Scan one or more playlists. Falls back to config's monitored_playlists if none given.

    Fetches playlist metadata to get the name, then scans each sequentially.
    Broadcasts overall progress so the frontend can show "Scanning 2 of 4..."
    """
    if not playlist_ids:
        playlist_ids = get_config().get("monitored_playlists", [])

    if not playlist_ids:
        raise ValueError(
            "No playlists to scan — provide playlist IDs or set monitored_playlists in config"
        )

    sp = await asyncio.to_thread(get_spotify)
    total_playlists = len(playlist_ids)
    results: list[dict[str, Any]] = []

    await manager.broadcast("scan_batch_started", {"total": total_playlists})

    for idx, pid in enumerate(playlist_ids):
        try:
            playlist_meta = await asyncio.to_thread(sp.playlist, pid, fields="id,name")
            name = playlist_meta["name"]

            await manager.broadcast("scan_batch_progress", {
                "current": idx + 1,
                "total": total_playlists,
                "playlist_name": name,
            })

            stats = await scan_playlist(pid, name)
            results.append(stats)
        except Exception as exc:
            logger.error("Failed to scan playlist %s: %s", pid, exc)
            results.append({
                "playlist": pid,
                "error": str(exc),
                "baseline": 0,
                "new": 0,
                "skipped_dup": 0,
                "skipped_itunes": 0,
                "errors": 1,
                "total": 0,
            })

    total_new = sum(r.get("new", 0) for r in results)
    await manager.broadcast("scan_batch_complete", {
        "playlists_scanned": len(results),
        "results": results,
    })
    notify_scan_complete(len(results), total_new)
    return results
