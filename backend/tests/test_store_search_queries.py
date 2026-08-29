"""Slice 2 journeys: Beatport and Traxsource search with the identity query.

These tests never hit Beatport, Traxsource, or the network. They stub
Playwright and inspect the search URL the browser would open.

Install the runner (from backend/):
    /Users/hassansaab/apps/wavvy/.venv/bin/pytest tests/test_store_search_queries.py
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

import beatport_browser
import traxsource_browser
from beatport_browser import BeatportBrowser
from store_match import build_search_query, parse_store_query
from traxsource_browser import TraxsourceBrowser

# Spotify row for carted track 2365. Search must not paste this credit blob.
SPOTIFY_ARTIST = "Amentia, Armen Miran"
SPOTIFY_TITLE = "Miracle D'Hwange - Armen Miran Remix"


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


def _decoded_param(url: str, name: str) -> str:
    values = parse_qs(urlparse(url).query).get(name, [])
    assert values, f"expected {name}= in {url}"
    return values[0]


def _goto_urls(
    browser: BeatportBrowser | TraxsourceBrowser,
    host: str,
    title: str,
    artist: str,
) -> list[str]:
    page = _FakePage(host)

    async def _fake_new_context() -> _FakeContext:
        return _FakeContext(page)

    browser._new_context = _fake_new_context  # type: ignore[method-assign]
    asyncio.run(browser.search(title, artist))
    return page.goto_urls


def _identity_query() -> str:
    return build_search_query(
        parse_store_query(artist=SPOTIFY_ARTIST, title=SPOTIFY_TITLE),
    )


def test_miracle_dhwange_searches_with_armen_miran_and_keeps_the_apostrophe() -> None:
    """Beatport search for this remix is title plus remixer, apostrophe kept."""
    expected = _identity_query()
    raw_blob = f"{SPOTIFY_ARTIST} {SPOTIFY_TITLE}".strip()
    urls = _goto_urls(
        BeatportBrowser(),
        "www.beatport.com",
        SPOTIFY_TITLE,
        SPOTIFY_ARTIST,
    )

    assert expected == "Miracle D'Hwange Armen Miran"
    assert "D'Hwange" in expected
    assert len(urls) == 1
    decoded = _decoded_param(urls[0], "q")
    assert decoded == expected
    assert quote_plus(expected) in urls[0]
    assert urls[0] == beatport_browser.SEARCH_URL_TEMPLATE.format(
        query=quote_plus(expected),
    )
    assert decoded != raw_blob
    assert "Amentia" not in decoded


def test_traxsource_miracle_dhwange_searches_with_armen_miran_and_keeps_the_apostrophe() -> None:
    """Traxsource search for this remix is title plus remixer, apostrophe kept."""
    expected = _identity_query()
    raw_blob = f"{SPOTIFY_ARTIST} {SPOTIFY_TITLE}".strip()
    urls = _goto_urls(
        TraxsourceBrowser(),
        "www.traxsource.com",
        SPOTIFY_TITLE,
        SPOTIFY_ARTIST,
    )

    assert expected == "Miracle D'Hwange Armen Miran"
    assert "D'Hwange" in expected
    assert len(urls) == 1
    decoded = _decoded_param(urls[0], "term")
    assert decoded == expected
    assert quote_plus(expected) in urls[0]
    assert urls[0] == traxsource_browser.SEARCH_URL_TEMPLATE.format(
        query=quote_plus(expected),
    )
    assert decoded != raw_blob
    assert "Amentia" not in decoded
