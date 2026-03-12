"""FastAPI application entry point for the DJ Track Pipeline."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import load_config, get_config, save_config, get_supabase_creds, get_spotify_creds
from database import init_supabase, validate_connection
from file_pipeline import pipeline
from itunes_bridge import is_music_app_running
from itunes_scanner import library_cache
from ws_manager import manager

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Starting DJ Track Pipeline backend")
    load_config()

    # Attempt Supabase connection (non-fatal if creds not yet configured)
    try:
        init_supabase()
        if validate_connection():
            logger.info("Supabase connection verified")
        else:
            logger.warning("Supabase reachable but query failed — check schema")
    except RuntimeError as exc:
        logger.warning("Supabase not configured yet: %s", exc)

    # Scan iTunes library in background thread (non-blocking)
    try:
        await asyncio.to_thread(library_cache.scan)
    except Exception as exc:
        logger.warning("iTunes library scan failed during startup: %s", exc)

    # Start file pipeline (watchdog on ~/Downloads)
    pipeline.start()

    yield

    pipeline.stop()
    logger.info("Shutting down DJ Track Pipeline backend")


app = FastAPI(title="DJ Track Pipeline", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health_check():
    """Return system health: Supabase, drive mount, Music app, iTunes cache."""
    drive_path = get_config().get("external_drive_path", "/Volumes/My Passport/Music/iTunes/iTunes Media/Music/Unknown Artist/Unknown Album")
    drive_mounted = Path(drive_path).exists()

    supabase_ok = False
    try:
        supabase_ok = await asyncio.to_thread(validate_connection)
    except Exception:
        pass

    music_app_running = await asyncio.to_thread(is_music_app_running)

    return {
        "supabase": supabase_ok,
        "drive_mounted": drive_mounted,
        "drive_path": drive_path,
        "music_app": music_app_running,
        "itunes_cache": library_cache.status(),
    }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config_endpoint():
    return get_config()


@app.put("/api/config")
async def update_config_endpoint(body: dict):
    current = get_config()

    for key, value in body.items():
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            current[key].update(value)
        else:
            current[key] = value
    save_config(current)

    return {"ok": True}


@app.get("/api/credentials/status")
async def credentials_status():
    """Report which env-based credentials are configured (without exposing values)."""
    sb = get_supabase_creds()
    sp = get_spotify_creds()
    return {
        "supabase_url": bool(sb["url"]),
        "supabase_anon_key": bool(sb["anon_key"]),
        "spotify_client_id": bool(sp["client_id"]),
        "spotify_client_secret": bool(sp["client_secret"]),
        "spotify_redirect_uri": bool(sp["redirect_uri"]),
    }


# ---------------------------------------------------------------------------
# Spotify OAuth
# ---------------------------------------------------------------------------

@app.get("/api/spotify/auth-url")
async def spotify_auth_url():
    """Return the Spotify OAuth URL for the user to authorize the app."""
    from spotify_monitor import get_auth_url

    try:
        url = get_auth_url()
        return {"url": url}
    except RuntimeError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/callback")
async def spotify_callback(code: str):
    """Exchange the authorization code for tokens, then redirect to frontend."""
    from fastapi.responses import RedirectResponse
    from spotify_monitor import exchange_code

    try:
        exchange_code(code)
        return RedirectResponse(url="http://localhost:5173/?spotify=connected", status_code=302)
    except Exception as exc:
        logger.error("Spotify callback failed: %s", exc)
        return RedirectResponse(url=f"http://localhost:5173/?spotify=error&detail={exc}", status_code=302)


@app.get("/api/spotify/token")
async def spotify_token():
    """Return a valid access token for the Spotify Web Playback SDK."""
    from fastapi import HTTPException
    from spotify_monitor import get_access_token

    try:
        token = await asyncio.to_thread(get_access_token)
        return {"access_token": token}
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@app.get("/api/spotify/status")
async def spotify_status():
    """Check whether the app has a valid cached Spotify token with required scopes."""
    from spotify_monitor import is_authenticated, has_streaming_scope
    authenticated = is_authenticated()
    return {
        "authenticated": authenticated,
        "has_streaming_scope": has_streaming_scope() if authenticated else False,
    }


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------

@app.get("/api/playlists")
async def list_playlists():
    """Return the current user's Spotify playlists."""
    from spotify_monitor import fetch_user_playlists
    return await asyncio.to_thread(fetch_user_playlists)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

@app.post("/api/scan")
async def scan_playlists(body: dict | None = None):
    """Scan Spotify playlists for new tracks.

    Body (optional): {"playlist_ids": ["id1", "id2"]}
    Falls back to config.monitored_playlists when no IDs provided.

    After the scan, automatically resolves Beatport/Traxsource links
    for any newly discovered tracks as a background task.
    """
    from fastapi import HTTPException
    from spotify_monitor import scan_monitored_playlists

    playlist_ids = (body or {}).get("playlist_ids")

    try:
        results = await scan_monitored_playlists(playlist_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    total_new = sum(r.get("new", 0) for r in results)
    if total_new > 0:
        asyncio.create_task(_auto_resolve_new_tracks())

    return {"ok": True, "results": results}


async def _auto_resolve_new_tracks() -> None:
    """Background task: resolve links for all tracks with status 'new'."""
    from link_resolver import resolve_tracks

    try:
        logger.info("Auto-resolving links for new tracks after scan")
        await resolve_tracks(None)
    except Exception as exc:
        logger.error("Auto link resolution failed: %s", exc)


# ---------------------------------------------------------------------------
# Tracks (basic CRUD — expanded in later phases)
# ---------------------------------------------------------------------------

ACTIVE_STATUSES = ["new", "approved", "carted", "cart_failed", "processing"]


@app.get("/api/tracks")
async def list_tracks(status: str | None = None, search: str | None = None):
    """List tracks. Filter by status or search by name/artist."""
    from database import get_tracks_by_status, get_tracks_by_statuses, search_tracks

    if search:
        return await asyncio.to_thread(search_tracks, search)
    if status:
        return await asyncio.to_thread(get_tracks_by_status, status)
    return await asyncio.to_thread(get_tracks_by_statuses, ACTIVE_STATUSES)


@app.get("/api/tracks/counts")
async def track_counts():
    """Return track counts grouped by status for dashboard badges."""
    from database import get_track_counts_by_status
    return await asyncio.to_thread(get_track_counts_by_status)


@app.patch("/api/tracks/{track_id}")
async def update_track(track_id: int, body: dict):
    from database import update_track_fields
    result = await asyncio.to_thread(update_track_fields, track_id, body)
    if not result:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": f"Track {track_id} not found"})
    return result


# ---------------------------------------------------------------------------
# Link Resolver
# ---------------------------------------------------------------------------

@app.post("/api/resolve")
async def resolve_links(body: dict | None = None):
    """Resolve Beatport/Traxsource links for tracks.

    Body (all fields optional)::

        {
            "track_ids": [1, 2, 3]   // specific tracks; omit to resolve all "new" tracks
        }

    Runs asynchronously in a background task; WebSocket events
    ``resolve_progress`` and ``resolve_complete`` stream updates.
    """
    from link_resolver import resolve_tracks

    track_ids = (body or {}).get("track_ids")
    result = await resolve_tracks(track_ids)
    return result


# ---------------------------------------------------------------------------
# Cart Builder
# ---------------------------------------------------------------------------

@app.post("/api/cart/build")
async def build_cart_endpoint(body: dict):
    """Launch a Playwright cart-building session for a store.

    Body: {"store": "beatport" | "traxsource"}

    Automatically resolves missing store links for approved tracks before
    starting the cart build.  Runs in a background thread so it doesn't
    block the event loop.  Progress streams via WebSocket events.
    """
    from fastapi import HTTPException
    from cart_builder import build_cart, is_running
    from database import get_tracks_by_status
    from link_resolver import resolve_tracks

    store = body.get("store")
    if store not in ("beatport", "traxsource"):
        raise HTTPException(
            status_code=400,
            detail="store must be 'beatport' or 'traxsource'",
        )

    if is_running(store):
        raise HTTPException(
            status_code=409,
            detail=f"Cart build already running for {store}",
        )

    url_field = "beatport_url" if store == "beatport" else "traxsource_url"
    approved = await asyncio.to_thread(get_tracks_by_status, "approved")

    if not approved:
        raise HTTPException(
            status_code=400,
            detail="No approved tracks. Select tracks and approve them first, then try again.",
        )

    unresolved = [t for t in approved if not t.get(url_field)]

    if unresolved:
        unresolved_ids = [t["id"] for t in unresolved]
        logger.info(
            "Auto-resolving links for %d approved track(s) before %s cart build",
            len(unresolved_ids), store,
        )
        await resolve_tracks(unresolved_ids)

    refreshed = await asyncio.to_thread(get_tracks_by_status, "approved")
    eligible = [t for t in refreshed if t.get(url_field)]
    if not eligible:
        store_label = "Beatport" if store == "beatport" else "Traxsource"
        raise HTTPException(
            status_code=400,
            detail=f"No approved tracks have {store_label} links. Resolve links first, then try again.",
        )

    asyncio.create_task(asyncio.to_thread(build_cart, store))

    return {"ok": True, "message": f"Cart build started for {store}"}


@app.get("/api/cart/status")
async def cart_status():
    """Check whether a cart build is currently running."""
    from cart_builder import is_running
    return {
        "beatport": is_running("beatport"),
        "traxsource": is_running("traxsource"),
    }


# ---------------------------------------------------------------------------
# iTunes Library
# ---------------------------------------------------------------------------

@app.get("/api/library/status")
async def library_status():
    """Return current state of the in-memory iTunes library cache."""
    return library_cache.status()


@app.post("/api/library/scan")
async def library_scan():
    """Trigger a fresh scan of the Apple Music library."""
    if library_cache.is_scanning:
        return {"ok": False, "message": "Scan already in progress"}
    count = await asyncio.to_thread(library_cache.scan)
    return {"ok": True, "track_count": count}


# ---------------------------------------------------------------------------
# iTunes Playlists
# ---------------------------------------------------------------------------

@app.get("/api/library/playlists")
async def library_playlists():
    """Return all user playlist names from Apple Music."""
    from itunes_bridge import get_all_playlists
    playlists = await asyncio.to_thread(get_all_playlists)
    return {"playlists": playlists}


@app.post("/api/tracks/{track_id}/add-to-playlists")
async def add_track_to_playlists(track_id: int):
    """Add an already-imported track to its target playlists in Apple Music.

    Useful when the user assigns playlists after the track is already ``done``.
    Reads the track's ``target_playlists`` from the database, then uses
    AppleScript to find the track in the library and duplicate it to each playlist.
    """
    from fastapi import HTTPException
    from database import get_supabase
    from itunes_bridge import add_existing_track_to_playlist, is_music_app_running as music_running

    if not await asyncio.to_thread(music_running):
        raise HTTPException(status_code=503, detail="Apple Music is not running")

    result = get_supabase().table("tracks").select("*").eq("id", track_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Track {track_id} not found")
    track = result.data[0]

    playlists: list[str] = track.get("target_playlists") or []
    if not playlists:
        raise HTTPException(status_code=400, detail="No target playlists assigned to this track")

    results: dict[str, bool] = {}
    for pl in playlists:
        ok = await asyncio.to_thread(
            add_existing_track_to_playlist,
            track["track_name"],
            track["artist_name"],
            pl,
        )
        results[pl] = ok

    return {"ok": True, "results": results}


# ---------------------------------------------------------------------------
# File Pipeline
# ---------------------------------------------------------------------------

@app.get("/api/pipeline/status")
async def pipeline_status():
    """Return current state of the file-watch pipeline."""
    return pipeline.status()


@app.post("/api/pipeline/assign")
async def pipeline_assign(body: dict):
    """Manually assign an unmatched file to a specific track.

    Body: {"filepath": "/path/to/file.wav", "track_id": 123}
    """
    from fastapi import HTTPException

    filepath = body.get("filepath")
    track_id = body.get("track_id")
    if not filepath or not track_id:
        raise HTTPException(status_code=400, detail="filepath and track_id are required")

    try:
        result = await asyncio.to_thread(pipeline.assign_file, filepath, int(track_id))
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
