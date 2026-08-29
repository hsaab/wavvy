"""File pipeline — watches ~/Downloads for new audio files and matches them.

Runs a Watchdog observer in a daemon thread. When a .wav or .mp3 lands in the
downloads folder, the pipeline:
  1. Waits for a stable file size (download complete).
  2. Fuzzy-matches the filename to buy-queue tracks in Supabase.
  3. If matched: sets status ``downloaded`` with ``download_path`` and broadcasts
     ``file_downloaded`` so the user can adjust playlists before importing.
  4. If unmatched: broadcasts a WebSocket event for manual assignment.

Files are moved as-is — never transcoded.

Manual ``process_track`` / API ``POST /api/pipeline/process`` then moves the file
to the external drive, imports to Apple Music, and marks ``done``.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from audio_match import (
    AUDIO_EXTENSIONS,
    OPEN_MATCH_STATUSES,
    consume_track,
    list_audio_files,
    pick_track_for_file,
)
from config import get_config
from database import get_tracks_by_status, get_tracks_by_statuses, update_track_status
from itunes_bridge import add_to_multiple_playlists, is_music_app_running
from playlist_targets import playlists_for_import
from itunes_scanner import library_cache
from notifications import notify_file_processed, notify_file_unmatched, notify_drive_unmounted
from ws_manager import manager

logger = logging.getLogger(__name__)

STABILITY_CHECKS = 3
STABILITY_INTERVAL = 1.0  # seconds between size checks
SCAN_STABLE_INTERVAL = 0.25  # one extra size sample; not the full watchdog wait
DEBOUNCE_COOLDOWN = 10.0  # seconds to ignore duplicate events for the same file
TEMP_EXTENSIONS = {".crdownload", ".part", ".tmp", ".download", ".partial"}


def _empty_scan(*, folder_missing: bool = False) -> dict[str, Any]:
    return {
        "count": 0,
        "matched": [],
        "unmatched": [],
        "files": [],
        "folder_missing": folder_missing,
    }


class _AudioHandler(FileSystemEventHandler):
    """Filesystem event handler that delegates audio files to the pipeline."""

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
        self._claim_lock = threading.RLock()

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

        handler = _AudioHandler(self)
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

    def scan_downloads(self) -> dict[str, Any]:
        """Match existing WAV/MP3 files in Downloads to buy-queue tracks."""
        cfg = get_config()
        downloads = Path(cfg.get("downloads_folder", "~/Downloads")).expanduser()
        if not downloads.is_dir():
            logger.warning("Downloads folder missing for scan: %s", downloads)
            return _empty_scan(folder_missing=True)

        files = list_audio_files(downloads)
        if not files:
            return _empty_scan()

        with self._claim_lock:
            return self._scan_files(files)

    def _scan_files(self, files: list[Path]) -> dict[str, Any]:
        open_candidates, downloaded = self._claim_pools()
        matched: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []

        for path in files:
            with self._seen_lock:
                self._seen[path.name] = time.time()
            try:
                row = self._scan_one(path, open_candidates, downloaded)
            except Exception as exc:
                logger.exception("Download scan failed for %s", path.name)
                unmatched.append({
                    "filename": path.name,
                    "matched": False,
                    "error": str(exc),
                })
                continue
            if row.get("matched"):
                matched.append(row)
            else:
                unmatched.append(row)

        logger.info(
            "Download scan complete — %d file(s), %d matched, %d unmatched",
            len(files),
            len(matched),
            len(unmatched),
        )
        return {
            "count": len(files),
            "matched": matched,
            "unmatched": unmatched,
            "files": [path.name for path in files],
            "folder_missing": False,
        }

    def _scan_one(
        self,
        path: Path,
        open_candidates: list[dict[str, Any]],
        downloaded: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not path.exists() or path.stat().st_size <= 0:
            return {"filename": path.name, "matched": False, "reason": "empty"}
        if not self._scan_file_ready(path):
            return {"filename": path.name, "matched": False, "reason": "unstable"}

        track, score, kind = pick_track_for_file(path, open_candidates, downloaded)
        if track is None:
            return {"filename": path.name, "matched": False, "score": score}

        consume_track(open_candidates, track["id"])
        consume_track(downloaded, track["id"])
        if kind != "existing":
            update_track_status(
                track["id"],
                "downloaded",
                {"download_path": str(path.resolve())},
            )
        logger.info(
            "Scan matched %s → %s - %s (kind=%s score=%d)",
            path.name, track["artist_name"], track["track_name"], kind, score,
        )
        return {
            "filename": path.name,
            "matched": True,
            "track_id": track["id"],
            "track_name": track["track_name"],
            "artist_name": track["artist_name"],
            "score": score,
            "kind": kind,
        }

    @staticmethod
    def _claim_pools() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            get_tracks_by_statuses(list(OPEN_MATCH_STATUSES)),
            get_tracks_by_status("downloaded"),
        )

    @staticmethod
    def _scan_file_ready(path: Path) -> bool:
        """True when size is > 0 and unchanged across one short sample."""
        if not path.exists():
            return False
        size = path.stat().st_size
        if size <= 0:
            return False
        if SCAN_STABLE_INTERVAL <= 0:
            return True
        time.sleep(SCAN_STABLE_INTERVAL)
        return path.exists() and path.stat().st_size == size

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
        """Return True if the file is a WAV/MP3 we should handle."""
        suffix = path.suffix.lower()
        if suffix in TEMP_EXTENSIONS:
            return False
        if suffix not in AUDIO_EXTENSIONS:
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
        """Watchdog path: wait for a stable file, then claim a queue row."""
        if not path.exists():
            logger.debug("File disappeared before processing: %s", path.name)
            return

        self._broadcast("file_detected", {"filename": path.name})

        if not self._wait_for_stable_size(path):
            logger.warning("File never stabilized: %s", path.name)
            return

        with self._claim_lock:
            open_candidates, downloaded = self._claim_pools()
            track, score, kind = pick_track_for_file(
                path, open_candidates, downloaded,
            )
            if track is not None and kind != "existing":
                update_track_status(
                    track["id"],
                    "downloaded",
                    {"download_path": str(path.resolve())},
                )

        if track is None:
            self.files_unmatched += 1
            logger.info("No match for %s — notifying UI", path.name)
            self._broadcast("file_unmatched", {
                "filename": path.name,
                "filepath": str(path),
            })
            notify_file_unmatched(path.name)
            return

        if kind == "existing":
            logger.info("Already assigned %s → track %s", path.name, track["id"])
            return

        logger.info(
            "Matched %s → %s - %s (kind=%s score=%d)",
            path.name, track["artist_name"], track["track_name"], kind, score,
        )
        self._broadcast("file_matched", {
            "filename": path.name,
            "track_id": track["id"],
            "track_name": track["track_name"],
            "artist_name": track["artist_name"],
            "score": score,
        })
        self._broadcast("file_downloaded", {
            "track_id": track["id"],
            "filename": path.name,
            "filepath": str(path.resolve()),
            "track_name": track["track_name"],
            "artist_name": track["artist_name"],
            "score": score,
        })
        logger.info(
            "Matched %s — status=downloaded (awaiting Process in UI)",
            path.name,
        )

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
    # File move
    # ------------------------------------------------------------------

    @staticmethod
    def _move_to_drive(src: Path, track: dict[str, Any]) -> Path:
        """Move the audio file to the external drive.

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
            raise RuntimeError(
                f"Apple Music is not running — cannot add {file_path.name} to the set playlist",
            )

        target_playlists = playlists_for_import(track)

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
    # Manual import (after user clicks Process)
    # ------------------------------------------------------------------

    def process_track(self, track_id: int) -> dict[str, Any]:
        """Move ``download_path`` to the drive, import to iTunes, mark ``done``."""
        with self._claim_lock:
            return self._process_track_locked(track_id)

    def _process_track_locked(self, track_id: int) -> dict[str, Any]:
        from database import get_supabase

        result = get_supabase().table("tracks").select("*").eq("id", track_id).execute()
        if not result.data:
            raise ValueError(f"Track {track_id} not found")
        track = result.data[0]

        if track.get("status") != "downloaded":
            raise ValueError(
                f"Track {track_id} is not awaiting processing (status={track.get('status')!r})",
            )

        raw_path = track.get("download_path") or ""
        if not raw_path:
            raise ValueError(f"Track {track_id} has no download_path")

        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"Download file missing: {raw_path}")

        update_track_status(track_id, "processing")
        self._broadcast("file_processing", {
            "track_id": track_id,
            "filename": path.name,
        })

        refreshed = get_supabase().table("tracks").select("*").eq("id", track_id).execute()
        if not refreshed.data:
            raise ValueError(f"Track {track_id} not found after status update")
        track = refreshed.data[0]

        try:
            dest = self._move_to_drive(path, track)
            self._import_to_itunes(dest, track)
            update_track_status(
                track_id,
                "done",
                {
                    "file_path": str(dest),
                    "download_path": None,
                    "target_playlists": playlists_for_import(track),
                },
            )
            self.files_processed += 1
            self._broadcast("file_complete", {
                "track_id": track_id,
                "track_name": track["track_name"],
                "artist_name": track["artist_name"],
                "destination": str(dest),
            })
            notify_file_processed(
                track.get("track_name", path.name),
                track.get("genre", "Unknown"),
            )
            logger.info("Manual process complete for track %s → %s", track_id, dest)
            return {"ok": True, "destination": str(dest)}

        except Exception as exc:
            logger.error("process_track failed for %s: %s", track_id, exc)
            restore = raw_path
            if path.exists():
                restore = str(path.resolve())
            update_track_status(track_id, "downloaded", {"download_path": restore})
            self._broadcast("file_error", {
                "track_id": track_id,
                "filename": path.name,
                "error": str(exc),
            })
            raise

    def process_all_downloaded(self) -> dict[str, Any]:
        """Process every track in ``downloaded`` status. Returns per-track outcomes."""
        with self._claim_lock:
            downloaded = get_tracks_by_status("downloaded")
            processed: list[int] = []
            errors: list[dict[str, Any]] = []
            for row in downloaded:
                tid = row["id"]
                try:
                    self._process_track_locked(tid)
                    processed.append(tid)
                except Exception as exc:
                    errors.append({"track_id": tid, "error": str(exc)})
            return {"processed": processed, "errors": errors, "count": len(processed)}

    # ------------------------------------------------------------------
    # Manual assignment (for unmatched files)
    # ------------------------------------------------------------------

    def assign_file(self, filepath: str, track_id: int) -> dict[str, Any]:
        """Manually assign an unmatched file to a track. Called from the API."""
        with self._claim_lock:
            return self._assign_file_locked(filepath, track_id)

    def _assign_file_locked(self, filepath: str, track_id: int) -> dict[str, Any]:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            raise ValueError("Only WAV and MP3 files are supported")

        from database import get_supabase
        result = get_supabase().table("tracks").select("*").eq("id", track_id).execute()
        if not result.data:
            raise ValueError(f"Track {track_id} not found")
        track = result.data[0]

        update_track_status(
            track_id,
            "downloaded",
            {"download_path": str(path.resolve())},
        )
        self._broadcast("file_downloaded", {
            "track_id": track_id,
            "filename": path.name,
            "filepath": str(path.resolve()),
            "track_name": track["track_name"],
            "artist_name": track["artist_name"],
        })
        logger.info("Assigned %s → track %s (status=downloaded)", path.name, track_id)
        return {"ok": True, "awaiting_process": True, "filepath": str(path.resolve())}

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
