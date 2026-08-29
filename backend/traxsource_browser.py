"""Async Playwright-based Traxsource search session for the link resolver.

Traxsource's search endpoint sits behind Cloudflare, so plain HTTP requests
(``httpx`` with a static User-Agent, etc.) now get a 403 ``Just a moment…``
interstitial — the same wall Beatport puts up. A real Chromium presenting a
normal browser fingerprint passes through cleanly.

This module is the Traxsource twin of :mod:`beatport_browser`. It shares the
exact same hard-won strategy:

* **Fresh context per search.** Cloudflare sets bot-mitigation cookies on the
  first response and escalates scoring on subsequent requests inside the same
  context. Throwing the context away between searches makes each lookup look
  like a first-time visitor, which Cloudflare lets through. (We verified a
  shared context fails on the 2nd request while fresh contexts succeed.)
* **No borrowed cookies.** The cart builder's saved ``cf_clearance`` is bound
  to a different fingerprint and only triggers a fresh challenge if reused.
* **Real desktop UA.** The default headless UA contains ``HeadlessChrome``,
  itself a strong bot signal.

Unlike Beatport (a Next.js SPA whose hits live in a ``__NEXT_DATA__`` JSON
payload), Traxsource renders ``.trk-row`` elements server-side. This module
only fetches HTML; parsing/fuzzy-matching stays in :mod:`link_resolver`.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote_plus, urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from store_match import build_search_query, parse_store_query

logger = logging.getLogger(__name__)

SEARCH_URL_TEMPLATE = "https://www.traxsource.com/search?term={query}"

NAV_TIMEOUT_MS = 30_000
# Traxsource renders matched tracks as ``.trk-row`` elements. Their presence
# means real search results; their absence (after the page settles) means
# either a genuine no-hits query or a Cloudflare interstitial.
RESULTS_SELECTOR = ".trk-row, .search-trk-row"
RESULTS_TIMEOUT_MS = 10_000

# A real desktop Chrome UA. The default headless UA contains "HeadlessChrome"
# which Cloudflare treats as a strong bot signal.
_DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_CHALLENGE_TITLE = "just a moment"
_CHALLENGE_MARKERS = ("/cdn-cgi/challenge-platform", "Performing security verification")


class TraxsourceBrowserError(RuntimeError):
    """Raised when the Playwright session cannot start or navigate."""


class TraxsourceChallengeError(TraxsourceBrowserError):
    """Raised when Traxsource never escapes Cloudflare's ``Just a moment…`` page.

    Surfacing this instead of returning the interstitial HTML matters: the
    parser would otherwise find no ``.trk-row`` elements and silently report
    "no match" for every track.
    """


def _is_cloudflare_challenge(page, html: str) -> bool:
    """Return True if the rendered page is Cloudflare's interstitial."""
    if "__cf_chl_rt_tk=" in (page.url or ""):
        return True
    lowered = html.lower()
    if _CHALLENGE_TITLE in lowered:
        return True
    return any(marker in html for marker in _CHALLENGE_MARKERS)


class TraxsourceBrowser:
    """Lazy-initialized async Chromium session for Traxsource search pages.

    Browser process is reused across the batch; each :meth:`search` gets its
    own short-lived ``BrowserContext`` so Cloudflare can't accumulate
    bot-mitigation state across requests.

    Not safe for concurrent use; create one instance per resolve batch and
    call :meth:`close` (or use as an async context manager) when finished.
    """

    def __init__(self) -> None:
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_browser(self) -> Browser:
        """Launch Chromium on first use and cache the browser process."""
        if self._browser is not None:
            return self._browser

        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(headless=True)
        except Exception as exc:
            await self.close()
            raise TraxsourceBrowserError(
                f"Failed to launch Chromium: {exc}"
            ) from exc

        return self._browser

    async def _new_context(self) -> BrowserContext:
        """Build a fresh anonymous context for a single search.

        Disposed by the caller after the search completes. We deliberately do
        NOT cache contexts: Cloudflare's per-context bot scoring would
        otherwise flag us mid-batch and start serving the JS challenge.
        """
        browser = await self._ensure_browser()
        try:
            return await browser.new_context(
                user_agent=_DESKTOP_UA,
                viewport={"width": 1280, "height": 900},
            )
        except Exception as exc:
            raise TraxsourceBrowserError(
                f"Failed to create browser context: {exc}"
            ) from exc

    async def close(self) -> None:
        """Tear down the browser. Safe to call more than once."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.debug("Error closing browser: %s", exc)
            self._browser = None

        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as exc:
                logger.debug("Error stopping playwright: %s", exc)
            self._pw = None

    async def __aenter__(self) -> "TraxsourceBrowser":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(self, title: str, artist: str) -> str:
        """Fetch Traxsource's search page for ``artist title`` and return HTML.

        Each call uses a fresh ``BrowserContext`` to avoid Cloudflare's
        per-context bot scoring.

        Raises:
          - :class:`TraxsourceBrowserError` on navigation failure.
          - :class:`TraxsourceChallengeError` if the page never escapes the
            Cloudflare interstitial.

        A page with no ``.trk-row`` elements but no challenge markers is a
        legitimate no-results page; we return its HTML and let the parser
        report no match.
        """
        context = await self._new_context()
        query = quote_plus(
            build_search_query(parse_store_query(artist=artist, title=title))
        )
        url = SEARCH_URL_TEMPLATE.format(query=query)

        try:
            page = await context.new_page()
            try:
                try:
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS,
                    )
                except PlaywrightTimeout as exc:
                    raise TraxsourceBrowserError(
                        f"Navigation timed out for {url}"
                    ) from exc
                except Exception as exc:
                    raise TraxsourceBrowserError(
                        f"Navigation failed for {url}: {exc}"
                    ) from exc

                final_host = urlparse(page.url).netloc
                if final_host and "traxsource.com" not in final_host:
                    raise TraxsourceBrowserError(
                        f"Traxsource redirected /search to {final_host}"
                    )

                # Wait for track rows. Absence is ambiguous (no hits vs.
                # interstitial), so we disambiguate via challenge markers.
                try:
                    await page.wait_for_selector(
                        RESULTS_SELECTOR, timeout=RESULTS_TIMEOUT_MS,
                    )
                except PlaywrightTimeout:
                    pass

                html = await page.content()

                rows_present = "trk-row" in html
                if not rows_present and _is_cloudflare_challenge(page, html):
                    raise TraxsourceChallengeError(
                        "Traxsource never escaped Cloudflare's 'Just a moment…' "
                        "interstitial. Headless browser fingerprint is being "
                        "flagged."
                    )

                return html
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
        finally:
            try:
                await context.close()
            except Exception:
                pass
