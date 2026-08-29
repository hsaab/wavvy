"""Library dump journeys: a comma inside an artist still pairs the track.

These tests never hit AppleScript or the network. They call ITunesLibraryCache.scan
with Music-running and dump helpers patched on the itunes_scanner module.

Install the runner (from backend/). System Python may be PEP 668 managed, so use a venv:
    python3 -m venv ../.venv
    ../.venv/bin/pip install -r requirements-dev.txt
Then:
    ../.venv/bin/pytest
"""

from __future__ import annotations

import pytest

from itunes_scanner import ITunesLibraryCache

# Music names and artists must be joined with a character that cannot appear
# in track metadata. Comma is not safe: the artist "Dazed, Nelav" contains one.
LIBRARY_DUMP_SEPARATOR = "\x1f"


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch) -> ITunesLibraryCache:
    monkeypatch.setattr("itunes_scanner.is_music_app_running", lambda: True)
    return ITunesLibraryCache()


def test_library_dump_with_comma_in_artist_still_pairs_the_correct_name_to_dazed_nelav(
    cache: ITunesLibraryCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dump that includes Dazed, Nelav still fuzzy-matches that artist to Midnight Sun."""

    def fake_dump(script: str) -> str:
        if "name of every track" in script:
            return LIBRARY_DUMP_SEPARATOR.join(["Midnight Sun", "Night Ride"])
        if "artist of every track" in script:
            return LIBRARY_DUMP_SEPARATOR.join(["Dazed, Nelav", "DJ Example"])
        return ""

    monkeypatch.setattr("itunes_scanner.run_applescript", fake_dump)

    cache.scan()

    assert cache.track_count == 2
    assert cache.contains_fuzzy("Dazed, Nelav", "Midnight Sun")
    assert cache.contains_fuzzy("DJ Example", "Night Ride")
    assert not cache.contains_fuzzy("Dazed, Nelav", "Night Ride")
