"""Slice 3 Traxsource search-query journeys.

These tests never launch Chromium and never hit the network. They stub the
browser context and read the URL search() would open.

Install the runner (from backend/):
    /Users/hassansaab/apps/wavvy/.venv/bin/pytest tests/test_traxsource_browser.py
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

from store_match import build_search_query, parse_store_query
from traxsource_browser import TraxsourceBrowser

LUYANA_ARTISTS = "Sahalé, Wanduta"
LUYANA_TITLE = "Luyana"
ELECTRIC_LOVE_ARTISTS = "Aiwaska, Starving Yet Full, Yulia Niko"
ELECTRIC_LOVE_TITLE = "Electric Love - Yulia Niko Remix"


class _FakePage:
    """Records goto URL and returns enough HTML to finish search()."""

    def __init__(self) -> None:
        self.goto_url: str | None = None
        self.url = ""

    async def goto(self, url: str, **kwargs) -> None:
        self.goto_url = url
        self.url = url

    async def wait_for_selector(self, *args, **kwargs) -> None:
        return None

    async def content(self) -> str:
        return '<html><div class="trk-row"></div></html>'

    async def close(self) -> None:
        return None


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def new_page(self) -> _FakePage:
        return self._page

    async def close(self) -> None:
        return None


def _search_term(title: str, artist: str) -> str:
    """Run Traxsource search() and return the decoded term= value."""
    page = _FakePage()
    browser = TraxsourceBrowser()

    async def _fake_new_context() -> _FakeContext:
        return _FakeContext(page)

    browser._new_context = _fake_new_context  # type: ignore[method-assign]
    asyncio.run(browser.search(title, artist))
    assert page.goto_url is not None, "search() never navigated"
    values = parse_qs(urlparse(page.goto_url).query).get("term", [])
    assert values, f"expected term= in {page.goto_url}"
    return values[0]


def test_luyana_traxsource_search_uses_title_plus_first_artist_not_the_full_credit_blob() -> None:
    """Sahalé, Wanduta / Luyana must search 'Luyana Sahalé', not the dumped credits."""
    term = _search_term(LUYANA_TITLE, LUYANA_ARTISTS)
    expected = build_search_query(
        parse_store_query(artist=LUYANA_ARTISTS, title=LUYANA_TITLE)
    )

    assert term == "Luyana Sahalé"
    assert term == expected
    assert "Wanduta" not in term
    assert term != f"{LUYANA_ARTISTS} {LUYANA_TITLE}"


def test_electric_love_remix_traxsource_search_uses_the_song_and_the_remixer() -> None:
    """A named remix searches the song plus remixer, not the full Spotify credit blob."""
    term = _search_term(ELECTRIC_LOVE_TITLE, ELECTRIC_LOVE_ARTISTS)
    expected = build_search_query(
        parse_store_query(artist=ELECTRIC_LOVE_ARTISTS, title=ELECTRIC_LOVE_TITLE)
    )

    assert term == "Electric Love Yulia Niko"
    assert term == expected
    assert "Aiwaska" not in term
    assert "Starving Yet Full" not in term
    assert term != f"{ELECTRIC_LOVE_ARTISTS} {ELECTRIC_LOVE_TITLE}"


def _traxsource_chromium_launch_kwargs() -> dict[str, Any]:
    """Stub Playwright and return Traxsource chromium.launch kwargs."""
    pw = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=MagicMock())
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)

    async def _run() -> None:
        await TraxsourceBrowser()._ensure_browser()

    with patch("traxsource_browser.async_playwright", return_value=starter):
        asyncio.run(_run())

    call = pw.chromium.launch.call_args
    assert call is not None, "Resolve Links Traxsource scrape never launched Chromium"
    return call.kwargs


def test_resolve_links_traxsource_scrape_launches_headed_chromium_not_headless_shell() -> None:
    """Resolve Links Traxsource scrape must use headed Chromium, not chromium_headless_shell."""
    launch_kwargs = _traxsource_chromium_launch_kwargs()
    assert launch_kwargs.get("headless") is False, (
        "Resolve Links Traxsource scrape must launch headed Chromium "
        "(headless=False); headless=True looks for chromium_headless_shell, "
        "which failed the live 10:55 Resolve Links run"
    )
