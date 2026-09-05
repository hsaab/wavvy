"""Playwright-based cart builder for Beatport.

Launches headed Google Chrome with a dedicated profile, waits for you to
log in (Beatport), then adds approved WAV tracks to the cart. Failures are
marked ``cart_failed`` without crashing the batch.

The Chrome profile lives in ``chrome_profile/`` so Cloudflare clearance and
login cookies survive across Cart BP clicks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Literal

from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
    TimeoutError as PlaywrightTimeout,
)

from database import get_tracks_by_status, update_track_status
from notifications import notify_cart_ready, notify_error
from ws_manager import manager

from store_selectors import (
    BEATPORT_BASE_URL,
    BEATPORT_CART_URL,
    BP_LOGIN_TRIGGER,
    BP_LOGGED_IN_INDICATOR,
    BP_FORMAT_DROPDOWN,
    BP_WAV_OPTION,
    BP_ADD_TO_CART,
    BP_COOKIE_ACCEPT,
    NAV_TIMEOUT_MS,
    ACTION_DELAY_SEC,
    PAGE_LOAD_WAIT_SEC,
    MANUAL_LOGIN_TIMEOUT_MS,
)

logger = logging.getLogger(__name__)

CHROME_PROFILE_DIR = Path(__file__).parent / "chrome_profile"
_HIDE_WEBDRIVER = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)

Store = Literal["beatport"]

_running: dict[str, bool] = {"beatport": False}

# FastAPI loop, captured on the app thread before Playwright runs in to_thread.
_loop: asyncio.AbstractEventLoop | None = None

_DESKTOP_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def is_running(store: Store) -> bool:
    """Check whether a cart-build session is already active for *store*."""
    return _running.get(store, False)


# ---------------------------------------------------------------------------
# Async WebSocket helper (safe to call from sync thread)
# ---------------------------------------------------------------------------

def _broadcast(event_type: str, payload: Any) -> None:
    """Fire-and-forget a WebSocket broadcast from the sync Playwright thread."""
    if _loop is not None and _loop.is_running():
        asyncio.run_coroutine_threadsafe(
            manager.broadcast(event_type, payload), _loop,
        )
    else:
        logger.warning("No running event loop for cart broadcast: %s", event_type)


# ---------------------------------------------------------------------------
# Cookie / consent banner
# ---------------------------------------------------------------------------

def _dismiss_cookie_banner(page: Page, selector: str) -> None:
    """Click the cookie-accept button if visible; ignore if absent."""
    try:
        btn = page.locator(selector).first
        if btn.is_visible(timeout=2_000):
            btn.click()
            time.sleep(0.5)
    except (PlaywrightTimeout, Exception):
        pass


# ---------------------------------------------------------------------------
# Beatport automation
# ---------------------------------------------------------------------------

def _cloudflare_blocking(page: Page) -> bool:
    try:
        title = (page.title() or "").lower()
        content = (page.content() or "").lower()
    except Exception:
        return False
    combined = f"{title} {content}"
    return "just a moment" in combined or "verify you are human" in combined


def _raise_cloudflare_block(page: Page) -> None:
    title = ""
    try:
        title = page.title() or ""
    except Exception:
        pass
    msg = (
        "Beatport is blocked by a Cloudflare human check "
        f"(page title: {title!r}). Complete the challenge in the browser window."
    )
    logger.error(msg)
    raise RuntimeError(msg) from None


def _wait_until_cloudflare_clears(page: Page) -> None:
    """Do not click anything while the human-check overlay is still up."""
    if not _cloudflare_blocking(page):
        return
    try:
        page.wait_for_function(
            "() => !/just a moment/i.test(document.title) "
            "&& !/verify you are human/i.test("
            "document.body ? document.body.innerText : '')",
            timeout=NAV_TIMEOUT_MS,
        )
    except PlaywrightTimeout:
        _raise_cloudflare_block(page)
    if _cloudflare_blocking(page):
        _raise_cloudflare_block(page)


def _wait_for_beatport_homepage(page: Page) -> None:
    """Wait for Log In or the logged-in avatar; name a Cloudflare human check on timeout."""
    _wait_until_cloudflare_clears(page)
    login_or_avatar = page.locator(BP_LOGIN_TRIGGER).first.or_(
        page.locator(BP_LOGGED_IN_INDICATOR).first,
    )
    try:
        login_or_avatar.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeout:
        if _cloudflare_blocking(page):
            _raise_cloudflare_block(page)
        raise
    if _cloudflare_blocking(page):
        _wait_until_cloudflare_clears(page)


def _open_beatport_homepage(page: Page) -> None:
    """Load www.beatport.com once and wait out Cloudflare before touching the page."""
    page.goto(BEATPORT_BASE_URL, timeout=NAV_TIMEOUT_MS)
    time.sleep(PAGE_LOAD_WAIT_SEC)
    _wait_for_beatport_homepage(page)
    _dismiss_cookie_banner(page, BP_COOKIE_ACCEPT)


def _beatport_avatar_visible(page: Page) -> bool:
    try:
        return page.locator(BP_LOGGED_IN_INDICATOR).first.is_visible(timeout=2_000)
    except PlaywrightTimeout:
        return False


def _wait_for_you_to_log_in_to_beatport(page: Page) -> None:
    """Do not type credentials. Wait until the avatar appears in the Chrome window."""
    if _beatport_avatar_visible(page):
        logger.info("Beatport session already logged in")
        return
    logger.info(
        "Waiting up to %ss for you to log in to Beatport in the Chrome window",
        MANUAL_LOGIN_TIMEOUT_MS // 1000,
    )
    try:
        page.locator(BP_LOGGED_IN_INDICATOR).first.wait_for(
            state="visible",
            timeout=MANUAL_LOGIN_TIMEOUT_MS,
        )
    except PlaywrightTimeout:
        raise RuntimeError(
            "Timed out waiting for you to log in to Beatport in the Chrome window."
        ) from None
    logger.info("Beatport login detected")


def _beatport_add_track(page: Page, track: dict[str, Any]) -> bool:
    """Navigate to a Beatport track URL, select WAV, add to cart."""
    url = track.get("beatport_url")
    if not url:
        logger.warning("Track %s has no beatport_url — skipping", track["id"])
        return False

    page.goto(url, timeout=NAV_TIMEOUT_MS)
    time.sleep(PAGE_LOAD_WAIT_SEC)

    try:
        dropdown = page.locator(BP_FORMAT_DROPDOWN).first
        if dropdown.is_visible(timeout=3_000):
            dropdown.click()
            time.sleep(0.5)
            page.locator(BP_WAV_OPTION).first.click()
            time.sleep(0.5)
    except PlaywrightTimeout:
        logger.debug("No format dropdown found for %s — may default to WAV", url)

    try:
        cart_btn = page.locator(BP_ADD_TO_CART).first
        cart_btn.wait_for(state="visible", timeout=10_000)
        cart_btn.click()
        time.sleep(ACTION_DELAY_SEC)
        return True
    except PlaywrightTimeout:
        logger.error("Add-to-cart button not found on %s", url)
        return False


# ---------------------------------------------------------------------------
# Orchestrator — runs the full cart-building session
# ---------------------------------------------------------------------------

def build_cart(store: Store) -> dict[str, Any]:
    """Build a cart on *store* for all ``approved`` tracks.

    This is a **synchronous, long-running** function designed to be invoked
    via ``asyncio.to_thread()`` from the FastAPI endpoint so it doesn't
    block the event loop.  Progress is streamed over WebSocket.

    Returns a summary dict: ``{total, added, failed, skipped, tracks}``.
    """
    if _running.get(store):
        return {"error": f"Cart build already running for {store}"}

    _running[store] = True
    _broadcast("cart_started", {"store": store})

    url_field = "beatport_url"
    tracks = get_tracks_by_status("approved")
    eligible = [t for t in tracks if t.get(url_field)]

    if not eligible:
        _running[store] = False
        _broadcast("cart_complete", {"store": store, "total": 0, "added": 0, "failed": 0})
        return {"total": 0, "added": 0, "failed": 0, "skipped": 0, "tracks": []}

    summary: dict[str, Any] = {
        "total": len(eligible),
        "added": 0,
        "failed": 0,
        "skipped": 0,
        "tracks": [],
    }

    try:
        with sync_playwright() as pw:
            context, page = _launch_browser(pw, store)
            try:
                _ensure_logged_in(page, store)

                for idx, track in enumerate(eligible, start=1):
                    track_label = f"{track.get('artist_name', '?')} – {track.get('track_name', '?')}"
                    _broadcast("cart_progress", {
                        "store": store,
                        "current": idx,
                        "total": len(eligible),
                        "track": track_label,
                        "track_id": track["id"],
                    })
                    logger.info("[%s] Adding %d/%d: %s", store, idx, len(eligible), track_label)

                    success = _add_track_to_cart(page, track, store)
                    new_status = "carted" if success else "cart_failed"
                    update_track_status(track["id"], new_status)

                    if success:
                        summary["added"] += 1
                    else:
                        summary["failed"] += 1
                    summary["tracks"].append({"id": track["id"], "status": new_status})

                    _broadcast("cart_track_result", {
                        "store": store,
                        "track_id": track["id"],
                        "success": success,
                        "new_status": new_status,
                        "current": idx,
                        "total": len(eligible),
                    })

                _broadcast("cart_complete", {
                    "store": store,
                    "total": summary["total"],
                    "added": summary["added"],
                    "failed": summary["failed"],
                })
                notify_cart_ready(store, summary["added"], summary["failed"])

                logger.info(
                    "[%s] Cart build complete: %d added, %d failed.",
                    store, summary["added"], summary["failed"],
                )
            finally:
                context.close()
    except Exception as exc:
        logger.exception("Cart build crashed for %s", store)
        _broadcast("cart_error", {"store": store, "error": str(exc)})
        notify_error("Cart Builder", f"{store}: {exc}")
        summary["error"] = str(exc)
    finally:
        _running[store] = False

    return summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _launch_browser(pw: Playwright, store: Store) -> tuple[BrowserContext, Page]:
    """Launch headed Google Chrome with a dedicated profile (not daily Chrome)."""
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        context = pw.chromium.launch_persistent_context(
            str(CHROME_PROFILE_DIR),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
            user_agent=_DESKTOP_CHROME_UA,
        )
    except Exception as exc:
        raise RuntimeError(
            "Cart BP needs Google Chrome installed. Playwright's bundled "
            "Chromium keeps hitting Cloudflare's human check."
        ) from exc
    context.add_init_script(_HIDE_WEBDRIVER)
    context.set_default_timeout(NAV_TIMEOUT_MS)
    page = context.pages[0] if context.pages else context.new_page()
    return context, page


def _ensure_logged_in(page: Page, store: Store) -> None:
    """Check session validity; wait for you to log in in Chrome."""
    _open_beatport_homepage(page)
    _wait_for_you_to_log_in_to_beatport(page)


def _add_track_to_cart(page: Page, track: dict[str, Any], store: Store) -> bool:
    """Add a track to the Beatport cart."""
    try:
        return _beatport_add_track(page, track)
    except Exception as exc:
        logger.error(
            "Exception adding track %s to %s cart: %s",
            track["id"], store, exc,
        )
        return False


def _navigate_to_cart(page: Page, store: Store) -> None:
    """Go to the cart / checkout page so the user can review."""
    cart_url = BEATPORT_CART_URL
    try:
        page.goto(cart_url, timeout=NAV_TIMEOUT_MS)
        time.sleep(PAGE_LOAD_WAIT_SEC)
    except PlaywrightTimeout:
        logger.warning("Timed out navigating to %s cart page", store)
