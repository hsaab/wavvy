"""iTunes / Apple Music library scanner for deduplication.

Scans the Music library via AppleScript at startup, holds results in an
in-memory set, and exposes fuzzy-match lookups so other modules can check
whether a track already exists in the local library before inserting it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from thefuzz import fuzz

from itunes_bridge import run_applescript, is_music_app_running

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 90


@dataclass
class LibraryEntry:
    """Normalized representation of a track in the Apple Music library."""
    name: str
    artist: str

    @property
    def key(self) -> str:
        return f"{self.artist.lower()}|||{self.name.lower()}"


@dataclass
class ITunesLibraryCache:
    """In-memory cache of the Apple Music library for dedup checks."""

    _entries: list[LibraryEntry] = field(default_factory=list)
    _keys: set[str] = field(default_factory=set)
    last_scan_time: float | None = None
    track_count: int = 0
    is_scanning: bool = False
    scan_error: str | None = None

    def scan(self) -> int:
        """Scan the full Apple Music library via AppleScript.

        Returns the number of tracks found.
        """
        self.is_scanning = True
        self.scan_error = None
        start = time.time()

        try:
            if not is_music_app_running():
                logger.warning("Music app is not running — skipping library scan")
                self.scan_error = "Music app is not running"
                return 0

            names_raw = run_applescript(
                'tell application "Music" to get name of every track of library playlist 1'
            )
            artists_raw = run_applescript(
                'tell application "Music" to get artist of every track of library playlist 1'
            )

            names = [n.strip() for n in names_raw.split(",")] if names_raw else []
            artists = [a.strip() for a in artists_raw.split(",")] if artists_raw else []

            if len(names) != len(artists):
                logger.warning(
                    "Name/artist count mismatch (%d vs %d) — using shorter list",
                    len(names), len(artists),
                )

            entries: list[LibraryEntry] = []
            keys: set[str] = set()
            for name, artist in zip(names, artists):
                entry = LibraryEntry(name=name, artist=artist)
                entries.append(entry)
                keys.add(entry.key)

            self._entries = entries
            self._keys = keys
            self.track_count = len(entries)
            self.last_scan_time = time.time()

            elapsed = self.last_scan_time - start
            logger.info(
                "iTunes library scan complete: %d tracks in %.1fs",
                self.track_count, elapsed,
            )
            return self.track_count

        except RuntimeError as exc:
            self.scan_error = str(exc)
            logger.error("iTunes library scan failed: %s", exc)
            return 0
        finally:
            self.is_scanning = False

    def contains_exact(self, artist: str, title: str) -> bool:
        """Check for an exact (case-insensitive) match."""
        key = f"{artist.lower()}|||{title.lower()}"
        return key in self._keys

    def contains_fuzzy(self, artist: str, title: str) -> bool:
        """Check for a fuzzy match above the configured threshold."""
        if self.contains_exact(artist, title):
            return True
        query = f"{artist} {title}".lower()
        for entry in self._entries:
            candidate = f"{entry.artist} {entry.name}".lower()
            if fuzz.token_sort_ratio(query, candidate) >= FUZZY_THRESHOLD:
                return True
        return False

    def add_entry(self, artist: str, title: str) -> None:
        """Add a track to the in-memory cache after it's been imported."""
        entry = LibraryEntry(name=title, artist=artist)
        self._entries.append(entry)
        self._keys.add(entry.key)
        self.track_count = len(self._entries)

    def status(self) -> dict:
        """Return a summary dict for the /api/library/status endpoint."""
        return {
            "track_count": self.track_count,
            "is_scanning": self.is_scanning,
            "last_scan_time": self.last_scan_time,
            "scan_error": self.scan_error,
        }


library_cache = ITunesLibraryCache()
