"""AppleScript bridge for Apple Music / iTunes integration.

All AppleScript execution is centralized here. Other modules call these
Python wrappers — never run osascript directly.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _escape_applescript_string(value: str) -> str:
    """Escape a Python string for safe embedding inside AppleScript double-quotes."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def run_applescript(script: str) -> str:
    """Execute an AppleScript string via osascript, returning stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.error("AppleScript failed (rc=%d): %s", result.returncode, stderr)
        raise RuntimeError(f"AppleScript error: {stderr}")
    return result.stdout.strip()


def is_music_app_running() -> bool:
    """Check whether Apple Music (or iTunes) is currently running."""
    script = (
        'tell application "System Events" to '
        '(name of processes) contains "Music"'
    )
    try:
        return run_applescript(script) == "true"
    except RuntimeError:
        return False


def launch_music_app() -> None:
    """Launch Apple Music if it is not already running."""
    if not is_music_app_running():
        run_applescript('tell application "Music" to activate')
        logger.info("Apple Music launched")


def get_all_playlists() -> list[str]:
    """Return the names of all user playlists in Apple Music."""
    script = 'tell application "Music" to get name of every user playlist'
    try:
        raw = run_applescript(script)
        if not raw:
            return []
        return [name.strip() for name in raw.split(",")]
    except RuntimeError:
        logger.warning("Could not retrieve playlists from Apple Music")
        return []


def ensure_playlist_exists(name: str) -> None:
    """Create a user playlist in Apple Music if it doesn't already exist."""
    safe_name = _escape_applescript_string(name)
    script = f'''
tell application "Music"
    if not (exists user playlist "{safe_name}") then
        make new user playlist with properties {{name:"{safe_name}"}}
    end if
end tell'''
    run_applescript(script)
    logger.info("Ensured playlist exists: %s", name)


def add_to_library(file_path: Path) -> None:
    """Import a file into the Apple Music library."""
    posix = str(file_path.resolve())
    safe_path = _escape_applescript_string(posix)
    script = f'''
tell application "Music"
    add POSIX file "{safe_path}"
end tell'''
    run_applescript(script)
    logger.info("Added to library: %s", file_path.name)


def add_to_playlist(file_path: Path, playlist_name: str) -> None:
    """Import a file into the library and add it to a specific playlist.

    Creates the playlist if it doesn't exist.
    """
    ensure_playlist_exists(playlist_name)
    posix = str(file_path.resolve())
    safe_path = _escape_applescript_string(posix)
    safe_playlist = _escape_applescript_string(playlist_name)
    script = f'''
tell application "Music"
    set theTrack to add POSIX file "{safe_path}"
    duplicate theTrack to user playlist "{safe_playlist}"
end tell'''
    run_applescript(script)
    logger.info("Added %s to playlist '%s'", file_path.name, playlist_name)


def add_to_multiple_playlists(file_path: Path, playlist_names: list[str]) -> dict[str, bool]:
    """Import a file into the library and add it to multiple playlists.

    Returns a dict of {playlist_name: success_bool} for each playlist.
    The file is imported once, then duplicated into each target playlist.
    """
    if not playlist_names:
        return {}

    for name in playlist_names:
        ensure_playlist_exists(name)

    posix = str(file_path.resolve())
    safe_path = _escape_applescript_string(posix)

    # Build AppleScript that imports once, then duplicates to each playlist
    duplicate_lines = "\n".join(
        f'    duplicate theTrack to user playlist "{_escape_applescript_string(name)}"'
        for name in playlist_names
    )
    script = f'''
tell application "Music"
    set theTrack to add POSIX file "{safe_path}"
{duplicate_lines}
end tell'''

    results: dict[str, bool] = {}
    try:
        run_applescript(script)
        for name in playlist_names:
            results[name] = True
        logger.info("Added %s to %d playlists", file_path.name, len(playlist_names))
    except RuntimeError as exc:
        logger.warning("Batch add failed, falling back to individual adds: %s", exc)
        for name in playlist_names:
            try:
                add_to_playlist(file_path, name)
                results[name] = True
            except RuntimeError:
                logger.error("Failed to add %s to playlist '%s'", file_path.name, name)
                results[name] = False

    return results


def add_existing_track_to_playlist(track_name: str, artist_name: str, playlist_name: str) -> bool:
    """Find a track already in the library and add it to a playlist.

    Searches by name and artist. Used for assigning playlists to tracks
    that were already imported.
    """
    ensure_playlist_exists(playlist_name)
    safe_name = _escape_applescript_string(track_name)
    safe_artist = _escape_applescript_string(artist_name)
    safe_playlist = _escape_applescript_string(playlist_name)
    script = f'''
tell application "Music"
    set matchedTracks to (every track whose name is "{safe_name}" and artist is "{safe_artist}")
    if (count of matchedTracks) > 0 then
        duplicate (item 1 of matchedTracks) to user playlist "{safe_playlist}"
        return "ok"
    else
        return "not_found"
    end if
end tell'''
    try:
        result = run_applescript(script)
        if result == "ok":
            logger.info("Added existing track '%s' to playlist '%s'", track_name, playlist_name)
            return True
        logger.warning("Track '%s - %s' not found in library", artist_name, track_name)
        return False
    except RuntimeError as exc:
        logger.error("Failed to add existing track to playlist: %s", exc)
        return False
