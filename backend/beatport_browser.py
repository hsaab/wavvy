"""Async Playwright-based Beatport search session for the link resolver.

Beatport's search endpoint sits behind Cloudflare, so plain HTTP requests
(``httpx``, ``curl_cffi`` with TLS impersonation, etc.) get a 403 ``Just a
moment…`` interstitial. A real Chromium that presents a normal browser
fingerprint sails through cleanly — Cloudflare only escalates to the JS
challenge when something looks off.

Why async, not sync
-------------------
The resolver runs inside FastAPI's asyncio event loop and processes
multiple tracks per batch. An earlier sync implementation called
``asyncio.to_thread(...)`` once per track, which rotated worker threads
and tripped Playwright's greenlet machinery with::

    greenlet.error: Cannot switch to a different thread

Async Playwright lives natively on the event loop and avoids that whole
class of bug. The cart builder still uses sync Playwright because its
entire flow runs in a single ``asyncio.to_thread`` call (one worker
thread for the whole cart build).

Cookie / context strategy
-------------------------
We launch the browser process once per batch but use a **fresh context per
search** — a new ``BrowserContext`` for every track lookup, no cookies
carried across.

Reasoning, in order of how we discovered each layer:

* Reusing the cart builder's ``cf_clearance`` cookie always fails. That
  token is bound to the ``(IP, User-Agent, TLS fingerprint)`` of the
  session that earned it; reusing it from a different headless context
  makes Cloudflare *force a fresh JS challenge* every time — much harder
  to clear than just being a first-time visitor.
* A single shared context (no cookies) clears the first request, then
  fails the rest. Cloudflare sets its own bot-mitigation cookies
  (``__cf_bm``, ``_cfuvid``) on the first response and uses them, plus
  client-hint tracking, to escalate scoring on subsequent requests.
* A fresh context per search wipes that state so each request looks like
  a first-time visitor and Cloudflare lets it through cleanly. We've
  verified ~1.7s per search with full ``search-all`` payloads back.

The context-per-search overhead (~100–200ms) is negligible against the
1.5s SCRAPE_DELAY_SECS the resolver already enforces between tracks.

If Beatport ever clamps down further on anonymous fingerprints, the next
step would be a stealth plugin (playwright-extra) or solving the challenge
in-page rather than going back to a borrowed cookie.
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

SEARCH_URL_TEMPLATE = "https://www.beatport.com/search?q={query}"

NAV_TIMEOUT_MS = 30_000
# Wait for the search-results section headings (Tracks/Releases) — only
# present once the SPA has rendered actual hits. If they never appear the
# page is either a 0-results homepage fallback or an auth/CF interstitial.
RESULTS_SELECTOR = "h2:has-text('Tracks'), h2:has-text('Releases')"
RESULTS_TIMEOUT_MS = 10_000

# A real desktop Chrome UA. The default headless UA contains "HeadlessChrome"
# which Cloudflare treats as a strong bot signal and uses to escalate the
# challenge.
_DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Markers we use to detect the "Just a moment..." Cloudflare interstitial.
# When any of these appear in the rendered HTML we know the page never made
# it past Cloudflare and we should not feed it to the parser.
_CHALLENGE_TITLE = "just a moment"
_CHALLENGE_MARKERS = ("/cdn-cgi/challenge-platform", "Performing security verification")


class BeatportBrowserError(RuntimeError):
    """Raised when the Playwright session cannot start or navigate."""


class BeatportLoginRedirectError(BeatportBrowserError):
    """Raised when ``/search`` redirects to the Beatport OAuth login flow.

    Should not normally happen now that we launch with a fresh anonymous
    context, but kept so callers can distinguish the failure mode if it
    ever resurfaces.
    """


class BeatportChallengeError(BeatportBrowserError):
    """Raised when Beatport returns the Cloudflare ``Just a moment…`` page.

    Indicates the headless Chromium fingerprint is being flagged. Surfacing
    this loudly is intentional — a silent challenge previously made every
    search look like a no-results hit, so links never got resolved.
    """


def _identity_query_text(title: str, artist: str) -> str:
    """Identity query from raw artist/title. Do not pass a prebuilt query here."""
    return build_search_query(parse_store_query(artist=artist, title=title))


def _is_cloudflare_challenge(page, html: str) -> bool:
    """Return True if the rendered page is Cloudflare's interstitial.

    Looks at three signals:
      * ``<title>`` is "Just a moment..." (the canonical CF interstitial title)
      * the URL was rewritten with the ``__cf_chl_rt_tk`` challenge token
      * the page body contains the ``challenge-platform`` script reference
    Any one of those is enough — they all indicate the page never escaped
    Cloudflare's challenge layer.
    """
    if "__cf_chl_rt_tk=" in (page.url or ""):
        return True
    lowered = html.lower()
    if _CHALLENGE_TITLE in lowered:
        return True
    return any(marker in html for marker in _CHALLENGE_MARKERS)


class BeatportBrowser:
    """Lazy-initialized async Chromium session for Beatport search pages.

    Browser process is reused across the batch (launching Chromium is
    ~1–2s of fixed overhead). Each :meth:`search` call gets its own
    short-lived ``BrowserContext`` so Cloudflare can't accumulate
    bot-mitigation state across requests — see the module docstring for
    why that matters.

    Not safe for concurrent use; create one instance per resolve batch
    and call :meth:`close` (or use as an async context manager) when
    finished.
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
            raise BeatportBrowserError(f"Failed to launch Chromium: {exc}") from exc

        return self._browser

    async def _new_context(self) -> BrowserContext:
        """Build a fresh anonymous context for a single search.

        Disposed by the caller after the search completes. We deliberately
        do NOT cache contexts: Cloudflare's per-context bot scoring would
        otherwise flag us mid-batch and start serving the JS challenge.
        """
        browser = await self._ensure_browser()
        try:
            return await browser.new_context(
                user_agent=_DESKTOP_UA,
                viewport={"width": 1280, "height": 900},
            )
        except Exception as exc:
            raise BeatportBrowserError(
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

    async def __aenter__(self) -> "BeatportBrowser":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(self, title: str, artist: str) -> str:
        """Fetch Beatport's search page for ``artist title`` and return the HTML.

        Each call uses a fresh ``BrowserContext`` to avoid Cloudflare's
        per-context bot scoring; see the module docstring for details.

        Raises:
          - :class:`BeatportBrowserError` on navigation failure.
          - :class:`BeatportLoginRedirectError` if ``/search`` redirects off
            ``www.beatport.com`` (e.g. into the OAuth flow).
          - :class:`BeatportChallengeError` if the page never escapes the
            Cloudflare ``Just a moment…`` interstitial. Surfacing this
            instead of returning the interstitial HTML matters because
            the parser would otherwise see no ``__NEXT_DATA__`` and
            silently report "no match" — exactly the bug we hit before.

        On success the HTML contains the real ``__NEXT_DATA__`` payload
        with a ``search-all`` query key.
        """
        context = await self._new_context()
        query = quote_plus(_identity_query_text(title, artist))
        url = SEARCH_URL_TEMPLATE.format(query=query)

        try:
            page = await context.new_page()
            try:
                try:
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS,
                    )
                except PlaywrightTimeout as exc:
                    raise BeatportBrowserError(
                        f"Navigation timed out for {url}"
                    ) from exc
                except Exception as exc:
                    raise BeatportBrowserError(
                        f"Navigation failed for {url}: {exc}"
                    ) from exc

                # Detect the OAuth bounce. If we land on account.beatport.com
                # the SPA never renders search results; bail loudly so the
                # caller can mark the batch instead of returning misleading HTML.
                final_host = urlparse(page.url).netloc
                if final_host and final_host != "www.beatport.com":
                    raise BeatportLoginRedirectError(
                        f"Beatport redirected /search to {final_host} "
                        f"(unexpected session state in the resolver context)"
                    )

                # Race "results visible" vs "still on the CF interstitial".
                # In the happy path, Tracks/Releases headings render in <2s.
                # In the unhappy path, the page sits on "Verification
                # successful. Waiting for www.beatport.com to respond" and
                # never transitions — Cloudflare's silent fingerprint reject.
                try:
                    await page.wait_for_selector(
                        RESULTS_SELECTOR, timeout=RESULTS_TIMEOUT_MS,
                    )
                except PlaywrightTimeout:
                    pass

                html = await page.content()

                # If the search-results headings are missing AND the page
                # still looks like the CF interstitial, this is the silent-
                # block case — raise so the resolver records a real failure.
                results_visible = (
                    "h2" in html.lower()
                    and (
                        "Tracks" in html or "Releases" in html
                    )
                )
                if not results_visible and _is_cloudflare_challenge(page, html):
                    raise BeatportChallengeError(
                        "Beatport never escaped Cloudflare's "
                        "'Just a moment…' interstitial. Headless browser "
                        "fingerprint is being flagged."
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
