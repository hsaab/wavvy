"""Slice 3 journeys: one identity query per store, not the Spotify credit blob.

These tests never hit Beatport, Traxsource, or the network. Resolver tests
record what link_resolver passes into mocked browsers. Browser tests stub
Playwright and inspect the search URL.

Install the runner (from backend/):
    ../.venv/bin/pytest tests/test_store_search_queries.py
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, quote_plus, urlparse

import beatport_browser
import link_resolver
import traxsource_browser
from beatport_browser import BeatportBrowser
from traxsource_browser import TraxsourceBrowser

GAB_RHOME_ARTISTS = "Gab Rhome, Mark Alow, Armen Miran"
BOB_FOSSIL_REMIX_TITLE = "Bob Fossil - Armen Miran Remix"
HRAACH_ARTISTS = "Hraach, Armen Miran"


class _RecordingBrowser:
    """Captures search() arguments; never opens Chromium or the network."""

    def __init__(self, html: str = "<html></html>") -> None:
        self.html = html
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def search(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append((args, kwargs))
        return self.html


class _FakePage:
    """Stand-in Playwright page that records goto URLs."""

    def __init__(self, host: str) -> None:
        self.goto_urls: list[str] = []
        self.url = f"https://{host}/"

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_urls.append(url)
        self.url = url

    async def wait_for_selector(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def content(self) -> str:
        return "<html><h2>Tracks</h2><h2>Releases</h2></html>"

    async def close(self) -> None:
        return None


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def new_page(self) -> _FakePage:
        return self._page

    async def close(self) -> None:
        return None


def _query_from_search_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Read the search text the resolver handed the browser.

    A single positional arg or query= is the identity string. Two-arg
    title, artist calls are the old blob path (artist then title).
    """
    if kwargs.get("query"):
        return str(kwargs["query"])
    if len(args) == 1:
        return str(args[0])
    if len(args) >= 2:
        title, artist = str(args[0]), str(args[1])
        return f"{artist} {title}".strip()
    if "title" in kwargs or "artist" in kwargs:
        return f"{kwargs.get('artist') or ''} {kwargs.get('title') or ''}".strip()
    raise AssertionError(
        f"search() was not given a query: args={args!r} kwargs={kwargs!r}"
    )


def _queries_sent(artist: str, title: str) -> tuple[str, str, int, int]:
    bp = _RecordingBrowser()
    ts = _RecordingBrowser()
    asyncio.run(link_resolver._beatport_search(bp, title, artist))
    asyncio.run(link_resolver._traxsource_search(ts, title, artist))
    assert bp.calls, "Beatport search was not called"
    assert ts.calls, "Traxsource search was not called"
    return (
        _query_from_search_call(*bp.calls[0]),
        _query_from_search_call(*ts.calls[0]),
        len(bp.calls),
        len(ts.calls),
    )


def _decoded_param(url: str, name: str) -> str:
    values = parse_qs(urlparse(url).query).get(name, [])
    assert values, f"expected {name}= in {url}"
    return values[0]


def _goto_urls(browser: BeatportBrowser | TraxsourceBrowser, host: str, title: str, artist: str) -> list[str]:
    page = _FakePage(host)

    async def _fake_new_context() -> _FakeContext:
        return _FakeContext(page)

    browser._new_context = _fake_new_context  # type: ignore[method-assign]
    asyncio.run(browser.search(title, artist))
    return page.goto_urls


def test_bob_fossil_searches_with_armen_miran_not_the_full_spotify_credit_blob() -> None:
    """Gab Rhome's remix must search 'Bob Fossil Armen Miran', not every credited name."""
    bp = _RecordingBrowser()
    ts = _RecordingBrowser()
    track = {
        "track_name": BOB_FOSSIL_REMIX_TITLE,
        "artist_name": GAB_RHOME_ARTISTS,
        "spotify_id": "",
    }
    with patch.object(link_resolver, "SCRAPE_DELAY_SECS", 0):
        asyncio.run(
            link_resolver.resolve_track(
                track, client=MagicMock(), bp_browser=bp, ts_browser=ts,
            )
        )

    assert len(bp.calls) == 1
    assert len(ts.calls) == 1
    expected = "Bob Fossil Armen Miran"
    assert _query_from_search_call(*bp.calls[0]) == expected
    assert _query_from_search_call(*ts.calls[0]) == expected


def test_echo_searches_with_roderic() -> None:
    """Holed Coin's remix must search 'Echo Roderic', not 'Holed Coin Echo - Roderic Remix'."""
    bp_q, ts_q, bp_n, ts_n = _queries_sent("Holed Coin", "Echo - Roderic Remix")

    assert bp_n == 1
    assert ts_n == 1
    assert bp_q == "Echo Roderic"
    assert ts_q == "Echo Roderic"


def test_nervous_layers_searches_with_hraach_not_every_collaborator() -> None:
    """A collab credit must not stuff Armen Miran into the search; first artist is enough."""
    bp_q, ts_q, bp_n, ts_n = _queries_sent(HRAACH_ARTISTS, "Nervous Layers")

    assert bp_n == 1
    assert ts_n == 1
    assert bp_q == "Nervous Layers Hraach"
    assert ts_q == "Nervous Layers Hraach"
    assert "Armen Miran" not in bp_q


def test_kigelia_searches_with_elfenbergs_name_not_mix_punctuation() -> None:
    """A plain Spotify title searches 'Kigelia Elfenberg'."""
    bp_q, ts_q, bp_n, ts_n = _queries_sent("Elfenberg", "Kigelia")

    assert bp_n == 1
    assert ts_n == 1
    assert bp_q == "Kigelia Elfenberg"
    assert ts_q == "Kigelia Elfenberg"


def test_bajo_el_cielo_azul_omits_original_mix_from_the_search() -> None:
    """Hyphenated Original Mix must not be copied into the store query."""
    bp_q, ts_q, bp_n, ts_n = _queries_sent(
        "Hraach", "Bajo El Cielo Azul - Original Mix",
    )

    assert bp_n == 1
    assert ts_n == 1
    assert bp_q == "Bajo El Cielo Azul Hraach"
    assert ts_q == "Bajo El Cielo Azul Hraach"
    assert "Original Mix" not in bp_q
    assert " - " not in bp_q


def test_mirage_searches_with_sam_shure() -> None:
    """A plain Spotify title searches 'Mirage Sam Shure'."""
    bp_q, ts_q, bp_n, ts_n = _queries_sent("Sam Shure", "Mirage")

    assert bp_n == 1
    assert ts_n == 1
    assert bp_q == "Mirage Sam Shure"
    assert ts_q == "Mirage Sam Shure"


def test_miracle_dhwange_searches_with_armen_miran_and_keeps_the_apostrophe() -> None:
    """Remix query is title plus remixer; the apostrophe stays until URL encoding."""
    bp_q, ts_q, bp_n, ts_n = _queries_sent(
        "Amentia", "Miracle D'Hwange - Armen Miran Remix",
    )

    assert bp_n == 1
    assert ts_n == 1
    expected = "Miracle D'Hwange Armen Miran"
    assert bp_q == expected
    assert ts_q == expected
    assert "D'Hwange" in bp_q


def test_an_empty_store_page_does_not_retry_with_the_title_only() -> None:
    """Zero hits still mean exactly one request per store, using the identity query."""
    bp = _RecordingBrowser()
    ts = _RecordingBrowser()
    asyncio.run(
        link_resolver._beatport_search(bp, BOB_FOSSIL_REMIX_TITLE, GAB_RHOME_ARTISTS)
    )
    asyncio.run(
        link_resolver._traxsource_search(ts, BOB_FOSSIL_REMIX_TITLE, GAB_RHOME_ARTISTS)
    )

    assert len(bp.calls) == 1
    assert len(ts.calls) == 1
    assert _query_from_search_call(*bp.calls[0]) == "Bob Fossil Armen Miran"
    assert _query_from_search_call(*ts.calls[0]) == "Bob Fossil Armen Miran"


def test_beatport_still_accepts_artist_and_title_but_does_not_search_the_raw_blob() -> None:
    """Old search(title, artist) callers must URL-encode the identity query, not artist+title."""
    urls = _goto_urls(
        BeatportBrowser(),
        "www.beatport.com",
        BOB_FOSSIL_REMIX_TITLE,
        GAB_RHOME_ARTISTS,
    )
    expected = "Bob Fossil Armen Miran"

    assert len(urls) == 1
    assert quote_plus(expected) in urls[0]
    assert _decoded_param(urls[0], "q") == expected
    assert urls[0] == beatport_browser.SEARCH_URL_TEMPLATE.format(
        query=quote_plus(expected),
    )


def test_traxsource_still_accepts_artist_and_title_but_does_not_search_the_raw_blob() -> None:
    """Old search(title, artist) callers must URL-encode the identity query, not artist+title."""
    urls = _goto_urls(
        TraxsourceBrowser(),
        "www.traxsource.com",
        "Echo - Roderic Remix",
        "Holed Coin",
    )
    expected = "Echo Roderic"

    assert len(urls) == 1
    assert quote_plus(expected) in urls[0]
    assert _decoded_param(urls[0], "term") == expected
    assert urls[0] == traxsource_browser.SEARCH_URL_TEMPLATE.format(
        query=quote_plus(expected),
    )


def test_beatport_url_encodes_unicode_only_at_the_browser_layer() -> None:
    """Miracle D'Hwange keeps its apostrophe in the query string; quote_plus encodes it."""
    urls = _goto_urls(
        BeatportBrowser(),
        "www.beatport.com",
        "Miracle D'Hwange - Armen Miran Remix",
        "Amentia",
    )
    expected = "Miracle D'Hwange Armen Miran"

    assert len(urls) == 1
    assert "D'Hwange" in expected
    assert quote_plus(expected) in urls[0]
    assert _decoded_param(urls[0], "q") == expected
    assert "Amentia" not in _decoded_param(urls[0], "q")
