"""Library dump journeys: a comma inside an artist still pairs the track.

These tests never hit AppleScript or the network. They call ITunesLibraryCache.scan
with Music-running patched. Most dumps patch run_applescript on itunes_scanner.
The trailing-empty and count-mismatch journeys mock subprocess.run instead so
scan goes through the real run_applescript strip.

Install the runner (from backend/). System Python may be PEP 668 managed, so use a venv:
    python3 -m venv ../.venv
    ../.venv/bin/pip install -r requirements-dev.txt
Then:
    ../.venv/bin/pytest
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

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


def _osascript_dump(
    names: list[str],
    artists: list[str],
) -> Callable[..., SimpleNamespace]:
    """Return a subprocess.run stand-in whose stdout is the raw Music dump plus a newline."""

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        script = cmd[2]
        if "name of every track" in script:
            stdout = LIBRARY_DUMP_SEPARATOR.join(names) + "\n"
        elif "artist of every track" in script:
            stdout = LIBRARY_DUMP_SEPARATOR.join(artists) + "\n"
        else:
            stdout = "\n"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    return fake_run


def test_library_dump_whose_artist_list_ends_with_empty_fields_still_keeps_name_and_artist_counts_aligned(
    cache: ITunesLibraryCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Music dump with trailing empty artists still pairs A Solas to Dazed, Nelav.

    library playlist 1 ends with untitled / no-artist items. run_applescript
    strips stdout, and U+001F is whitespace in Python, so trailing empty
    artist fields disappear unless scan preserves them.
    """
    names = [
        "Warm Up",
        "Night Ride",
        "Midnight Sun",
        "A Solas",
        "Loose Ends",
        "Untitled",
        "Untitled 2",
        "Untitled 3",
    ]
    artists = [
        "House Crew",
        "DJ Example",
        "Other Act",
        "Dazed, Nelav",
        "Closer",
        "",
        "",
        "",
    ]
    monkeypatch.setattr("itunes_bridge.subprocess.run", _osascript_dump(names, artists))

    cache.scan()

    assert cache.track_count == 8
    assert cache.contains_fuzzy("Dazed, Nelav", "A Solas")
    assert cache.contains_fuzzy("", "Untitled")
    assert cache.contains_fuzzy("", "Untitled 3")
    assert not cache.contains_fuzzy("Dazed, Nelav", "Night Ride")


def test_library_dump_with_unequal_name_and_artist_counts_does_not_build_a_crooked_cache(
    cache: ITunesLibraryCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Music returns more names than artists, the cache stays empty.

    A truncated dump must not zip the shorter list. That would pair the
    wrong name to Dazed, Nelav and let a later lookup fuzzy-match the
    wrong track.
    """
    names = ["A Solas", "Midnight Sun", "Other Cut"]
    artists = ["Dazed, Nelav", "DJ Example"]
    monkeypatch.setattr("itunes_bridge.subprocess.run", _osascript_dump(names, artists))

    cache.scan()

    assert cache.scan_error is not None
    assert cache.track_count == 0
    assert not cache.contains_fuzzy("Dazed, Nelav", "A Solas")
    assert not cache.contains_fuzzy("DJ Example", "Midnight Sun")
    assert not cache.contains_fuzzy("Dazed, Nelav", "Other Cut")
