"""macOS system notifications for key pipeline events.

Uses pync (which wraps terminal-notifier) when available,
falls back to osascript for basic notification support.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

_USE_PYNC = True

try:
    import pync
except ImportError:
    _USE_PYNC = False
    logger.info("pync not installed — falling back to osascript for notifications")

APP_TITLE = "DJ Track Pipeline"


def _notify_osascript(title: str, message: str) -> None:
    """Send a notification via osascript as a pync fallback."""
    escaped_title = title.replace('"', '\\"')
    escaped_msg = message.replace('"', '\\"')
    script = (
        f'display notification "{escaped_msg}" '
        f'with title "{escaped_title}"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        logger.debug("osascript notification failed: %s", exc)


def notify(title: str, message: str, *, group: str | None = None) -> None:
    """Send a macOS notification. Non-blocking, never raises."""
    try:
        if _USE_PYNC:
            kwargs = {"title": title, "group": group or APP_TITLE}
            pync.notify(message, **kwargs)
        else:
            _notify_osascript(title, message)
    except Exception as exc:
        logger.debug("Notification failed: %s", exc)


def notify_scan_complete(playlist_count: int, new_tracks: int) -> None:
    """Notify when a playlist scan finishes."""
    notify(
        "Scan Complete",
        f"Scanned {playlist_count} playlist(s) — {new_tracks} new track(s) found",
        group="scan",
    )


def notify_cart_ready(store: str, track_count: int, failed: int) -> None:
    """Notify when cart building finishes."""
    msg = f"{track_count} track(s) added to {store} cart"
    if failed > 0:
        msg += f" ({failed} failed)"
    notify("Cart Ready", msg, group="cart")


def notify_file_processed(track_name: str, genre: str) -> None:
    """Notify when a file is moved and imported to iTunes."""
    notify(
        "Track Processed",
        f'"{track_name}" → {genre}',
        group="file",
    )


def notify_file_unmatched(filename: str) -> None:
    """Notify when a downloaded WAV doesn't match any carted track."""
    notify(
        "Unmatched File",
        f'"{filename}" needs manual assignment',
        group="file",
    )


def notify_error(context: str, detail: str) -> None:
    """Notify on critical errors (drive unmounted, Supabase down, etc.)."""
    notify(
        f"Error: {context}",
        detail,
        group="error",
    )


def notify_drive_unmounted(path: str) -> None:
    """Notify when the external drive is not mounted during a file op."""
    notify_error("Drive Not Mounted", f"{path} is not available")
