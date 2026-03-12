"""File pipeline — watches ~/Downloads for new WAV files and processes them.

Runs a Watchdog observer in a daemon thread. When a .wav lands in the
downloads folder, the pipeline:
  1. Waits for a stable file size (download complete).
  2. Fuzzy-matches the filename to ``carted`` tracks in Supabase.
  3. If matched: moves the file to the external drive genre folder, imports into
     Apple Music + the correct playlist, updates Supabase status to ``done``.
  4. If unmatched: broadcasts a WebSocket event for manual assignment.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from thefuzz import fuzz
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from config import get_config
from database import get_tracks_by_status, update_track_status
from itunes_bridge import add_to_playlist, add_to_multiple_playlists, is_music_app_running
from itunes_scanner import library_cache
from notifications import notify_file_processed, notify_file_unmatched, notify_drive_unmounted
from ws_manager import manager

logger = logging.getLogger(__name__)

STABILITY_CHECKS = 3
STABILITY_INTERVAL = 1.0  # seconds between size checks
DEBOUNCE_COOLDOWN = 10.0  # seconds to ignore duplicate events for the same file
TEMP_EXTENSIONS = {".crdownload", ".part", ".tmp", ".download", ".partial"}


def _normalize_filename(name: str) -> str:
    """Strip common Beatport/Traxsource filename noise for matching."""
    name = Path(name).stem
    # Remove parenthetical mix labels: "(Original Mix)", "(Extended Mix)", etc.
    name = re.sub(r"\s*\(.*?\)\s*", " ", name)
    # Remove bracket tags: "[WAV]", "[320]", etc.
    name = re.sub(r"\s*\[.*?\]\s*", " ", name)
    # Collapse whitespace and lowercase
    return re.sub(r"\s+", " ", name).strip().lower()


def _split_artist_title(filename: str) -> tuple[str, str]:
    """Split a Beatport-style filename into (artist, title).

    Beatport convention: ``Artist - Track Title (Mix).wav``
    Falls back to ("", full_stem) when no separator is found.
    """
    stem = Path(filename).stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return "", stem.strip()


def _score_against_track(filename: str, track: dict[str, Any]) -> int:
    """Return a 0-100 fuzzy score for how well *filename* matches *track*."""
    norm = _normalize_filename(filename)
    artist_part, title_part = _split_artist_title(filename)

    db_artist = (track.get("artist_name") or "").lower()
    db_title = (track.get("track_name") or "").lower()
    db_combined = f"{db_artist} {db_title}".strip()

    scores: list[int] = []

    # Full normalized filename vs DB combined
    scores.append(fuzz.token_sort_ratio(norm, db_combined))

    # If we extracted artist/title, compare parts individually
    if artist_part:
        artist_score = fuzz.token_sort_ratio(artist_part.lower(), db_artist)
        title_norm = _normalize_filename(title_part)
        title_score = fuzz.token_sort_ratio(title_norm, db_title)
        # Weighted: title matters more than artist for matching
        scores.append(int(artist_score * 0.35 + title_score * 0.65))

    return max(scores)


MATCH_THRESHOLD = 80


class _WavHandler(FileSystemEventHandler):
    """Filesystem event handler that delegates WAV files to the pipeline."""

    def __init__(self, pipeline: FilePipeline) -> None:
        self._pipeline = pipeline

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._pipeline.enqueue(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle Firefox-style temp→final rename."""
        if not event.is_directory:
            self._pipeline.enqueue(Path(event.dest_path))


class FilePipeline:
    """Manages the Watchdog observer and file processing queue."""

    def __init__(self) -> None:
        self._observer: Observer | None = None
        self._processing_thread: threading.Thread | None = None
        self._queue: list[Path] = []
        self._queue_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

        # Debounce: filename → last-seen timestamp
        self._seen: dict[str, float] = {}
        self._seen_lock = threading.Lock()

        # Stats
        self.is_running = False
        self.files_processed = 0
        self.files_unmatched = 0
        self.last_event_time: float | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the watchdog observer and the processing loop thread."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        cfg = get_config()
        if not cfg.get("file_watch_enabled", True):
            logger.info("File watch disabled in config — pipeline not started")
            return

        downloads = Path(cfg.get("downloads_folder", "~/Downloads")).expanduser()
        if not downloads.is_dir():
            logger.error("Downloads folder does not exist: %s", downloads)
            return

        handler = _WavHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(downloads), recursive=False)
        self._observer.daemon = True
        self._observer.start()

        self._stop_event.clear()
        self._processing_thread = threading.Thread(
            target=self._processing_loop, daemon=True, name="file-pipeline",
        )
        self._processing_thread.start()

        self.is_running = True
        logger.info("File pipeline started — watching %s", downloads)

    def stop(self) -> None:
        """Stop the watchdog observer and processing thread."""
        self._stop_event.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        if self._processing_thread:
            self._processing_thread.join(timeout=5)
            self._processing_thread = None
        self.is_running = False
        logger.info("File pipeline stopped")

    def status(self) -> dict[str, Any]:
        """Return pipeline state for the API."""
        cfg = get_config()
        return {
            "is_running": self.is_running,
            "watch_enabled": cfg.get("file_watch_enabled", True),
            "downloads_folder": cfg.get("downloads_folder", "~/Downloads"),
            "files_processed": self.files_processed,
            "files_unmatched": self.files_unmatched,
            "last_event_time": self.last_event_time,
            "queue_size": len(self._queue),
        }

    # ------------------------------------------------------------------
    # Queueing / debounce
    # ------------------------------------------------------------------

    def enqueue(self, path: Path) -> None:
        """Add a file to the processing queue if it passes filters."""
        if not self._should_process(path):
            return

        name = path.name
        now = time.time()
        with self._seen_lock:
            last = self._seen.get(name, 0)
            if now - last < DEBOUNCE_COOLDOWN:
                logger.debug("Debounced duplicate event for %s", name)
                return
            self._seen[name] = now

        self.last_event_time = now
        with self._queue_lock:
            self._queue.append(path)
        logger.info("Queued for processing: %s", name)

    def _should_process(self, path: Path) -> bool:
        """Return True if the file is a WAV we should handle."""
        suffix = path.suffix.lower()
        if suffix in TEMP_EXTENSIONS:
            return False
        if suffix != ".wav":
            return False
        if path.name.startswith("."):
            return False
        return True

    # ------------------------------------------------------------------
    # Processing loop (runs in its own thread)
    # ------------------------------------------------------------------

    def _processing_loop(self) -> None:
        """Drain the queue and process files until stopped."""
        while not self._stop_event.is_set():
            path: Path | None = None
            with self._queue_lock:
                if self._queue:
                    path = self._queue.pop(0)

            if path is None:
                self._stop_event.wait(timeout=0.5)
                continue

            try:
                self._process_file(path)
            except Exception:
                logger.exception("Unexpected error processing %s", path.name)

    def _process_file(self, path: Path) -> None:
        """Process a single WAV file end-to-end."""
        if not path.exists():
            logger.debug("File disappeared before processing: %s", path.name)
            return

        self._broadcast("file_detected", {"filename": path.name})

        # Wait for download to finish (stable file size)
        if not self._wait_for_stable_size(path):
            logger.warning("File never stabilized: %s", path.name)
            return

        # Try to match against carted tracks
        track, score = self._match_to_track(path.name)

        if track is None:
            self.files_unmatched += 1
            logger.info("No match for %s — notifying UI", path.name)
            self._broadcast("file_unmatched", {
                "filename": path.name,
                "filepath": str(path),
            })
            notify_file_unmatched(path.name)
            return

        logger.info(
            "Matched %s → %s - %s (score=%d)",
            path.name, track["artist_name"], track["track_name"], score,
        )
        self._broadcast("file_matched", {
            "filename": path.name,
            "track_id": track["id"],
            "track_name": track["track_name"],
            "artist_name": track["artist_name"],
            "score": score,
        })

        # Transition: carted → processing
        update_track_status(track["id"], "processing")
        self._broadcast("file_processing", {
            "track_id": track["id"],
            "filename": path.name,
        })

        try:
            dest = self._move_to_drive(path, track)
            self._import_to_itunes(dest, track)

            update_track_status(track["id"], "done", {"file_path": str(dest)})
            self.files_processed += 1

            self._broadcast("file_complete", {
                "track_id": track["id"],
                "track_name": track["track_name"],
                "artist_name": track["artist_name"],
                "destination": str(dest),
            })
            notify_file_processed(
                track.get("track_name", path.name),
                track.get("genre", "Unknown"),
            )
            logger.info("Pipeline complete for %s → %s", path.name, dest)

        except Exception as exc:
            logger.error("Pipeline failed for %s: %s", path.name, exc)
            update_track_status(track["id"], "carted")
            self._broadcast("file_error", {
                "track_id": track["id"],
                "filename": path.name,
                "error": str(exc),
            })

    # ------------------------------------------------------------------
    # Stability check
    # ------------------------------------------------------------------

    @staticmethod
    def _wait_for_stable_size(path: Path) -> bool:
        """Return True once file size stops changing, False on timeout."""
        previous_size = -1
        stable_count = 0
        for _ in range(STABILITY_CHECKS * 4):
            if not path.exists():
                return False
            current_size = path.stat().st_size
            if current_size == previous_size and current_size > 0:
                stable_count += 1
                if stable_count >= STABILITY_CHECKS:
                    return True
            else:
                stable_count = 0
            previous_size = current_size
            time.sleep(STABILITY_INTERVAL)
        return False

    # ------------------------------------------------------------------
    # Track matching
    # ------------------------------------------------------------------

    @staticmethod
    def _match_to_track(filename: str) -> tuple[dict[str, Any] | None, int]:
        """Fuzzy-match *filename* against carted tracks in Supabase.

        Returns (best_matching_track, score) or (None, 0).
        """
        carted = get_tracks_by_status("carted")
        if not carted:
            return None, 0

        best_track: dict[str, Any] | None = None
        best_score = 0
        for track in carted:
            score = _score_against_track(filename, track)
            if score > best_score:
                best_score = score
                best_track = track

        if best_score >= MATCH_THRESHOLD:
            return best_track, best_score
        return None, best_score

    # ------------------------------------------------------------------
    # File move
    # ------------------------------------------------------------------

    @staticmethod
    def _move_to_drive(src: Path, track: dict[str, Any]) -> Path:
        """Move the WAV file to the external drive.

        Returns the final destination Path.
        Raises RuntimeError if the drive is not mounted.
        """
        cfg = get_config()
        dest_dir = Path(cfg.get("external_drive_path", "/Volumes/My Passport/Music/iTunes/iTunes Media/Music/Unknown Artist/Unknown Album"))

        if not dest_dir.exists():
            notify_drive_unmounted(str(dest_dir))
            raise RuntimeError(f"External drive not mounted at {dest_dir}")

        dest = dest_dir / src.name
        if dest.exists():
            stem = src.stem
            suffix = src.suffix
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{stem} ({counter}){suffix}"
                counter += 1

        shutil.move(str(src), str(dest))
        logger.info("Moved %s → %s", src.name, dest)
        return dest

    # ------------------------------------------------------------------
    # iTunes import
    # ------------------------------------------------------------------

    @staticmethod
    def _import_to_itunes(file_path: Path, track: dict[str, Any]) -> None:
        """Add the file to Apple Music library and all target playlists."""
        if not is_music_app_running():
            logger.warning("Music app not running — skipping iTunes import for %s", file_path.name)
            return

        target_playlists: list[str] = track.get("target_playlists") or []

        if not target_playlists:
            # Fall back to genre-based mapping for backward compatibility
            cfg = get_config()
            genre = track.get("genre") or ""
            playlist_mapping: dict[str, str] = cfg.get("playlist_mapping", {})
            fallback = playlist_mapping.get(genre, "")
            if fallback:
                target_playlists = [fallback]

        if target_playlists:
            add_to_multiple_playlists(file_path, target_playlists)
        else:
            from itunes_bridge import add_to_library
            add_to_library(file_path)
            logger.info("No target playlists — added to library only")

        library_cache.add_entry(
            artist=track.get("artist_name", ""),
            title=track.get("track_name", ""),
        )

    # ------------------------------------------------------------------
    # Manual assignment (for unmatched files)
    # ------------------------------------------------------------------

    def assign_file(self, filepath: str, track_id: int) -> dict[str, Any]:
        """Manually assign an unmatched file to a track. Called from the API."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        if path.suffix.lower() != ".wav":
            raise ValueError("Only WAV files are supported")

        # Fetch the track
        from database import get_supabase
        result = get_supabase().table("tracks").select("*").eq("id", track_id).execute()
        if not result.data:
            raise ValueError(f"Track {track_id} not found")
        track = result.data[0]

        update_track_status(track_id, "processing")
        self._broadcast("file_processing", {
            "track_id": track_id,
            "filename": path.name,
        })

        try:
            dest = self._move_to_drive(path, track)
            self._import_to_itunes(dest, track)
            update_track_status(track_id, "done", {"file_path": str(dest)})
            self.files_processed += 1

            self._broadcast("file_complete", {
                "track_id": track_id,
                "track_name": track["track_name"],
                "artist_name": track["artist_name"],
                "destination": str(dest),
            })
            return {"ok": True, "destination": str(dest)}

        except Exception as exc:
            update_track_status(track_id, "carted")
            self._broadcast("file_error", {
                "track_id": track_id,
                "filename": path.name,
                "error": str(exc),
            })
            raise

    # ------------------------------------------------------------------
    # WebSocket helper (bridges sync thread → async broadcast)
    # ------------------------------------------------------------------

    def _broadcast(self, event_type: str, payload: Any) -> None:
        """Fire-and-forget broadcast from a sync context."""
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(event_type, payload), self._loop,
            )
        else:
            logger.debug("No event loop for broadcast: %s", event_type)


pipeline = FilePipeline()
