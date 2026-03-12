"""Playwright-based cart builder for Beatport and Traxsource.

Launches a headless Chromium browser, logs in, iterates approved tracks,
selects WAV format, and adds each to the cart.  Failures are marked
``cart_failed`` without crashing the batch.

Session cookies are persisted to ``browser_state.json`` so repeated runs
don't require re-login.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
    TimeoutError as PlaywrightTimeout,
)

from database import get_tracks_by_status, update_track_status
from notifications import notify_cart_ready, notify_error
from ws_manager import manager

import json as _j
import time as _t
_log_path = "/Users/hassansaab/apps/music-is-the-answer/.cursor/debug-76d8a3.log"
def _dlog(loc, msg, data, hyp):
    try:
        with open(_log_path, "a") as _f:
            _f.write(_j.dumps({"sessionId":"76d8a3","location":loc,"message":msg,"data":data,"hypothesisId":hyp,"timestamp":int(_t.time()*1000)}) + "\n")
    except Exception:
        pass
from store_selectors import (
    BEATPORT_BASE_URL,
    BEATPORT_LOGIN_URL,
    BEATPORT_CART_URL,
    BP_EMAIL_INPUT,
    BP_PASSWORD_INPUT,
    BP_LOGIN_BUTTON,
    BP_LOGGED_IN_INDICATOR,
    BP_FORMAT_DROPDOWN,
    BP_WAV_OPTION,
    BP_ADD_TO_CART,
    BP_COOKIE_ACCEPT,
    TRAXSOURCE_BASE_URL,
    TRAXSOURCE_LOGIN_URL,
    TRAXSOURCE_CART_URL,
    TS_EMAIL_INPUT,
    TS_PASSWORD_INPUT,
    TS_LOGIN_BUTTON,
    TS_LOGGED_IN_INDICATOR,
    TS_WAV_BUY_BUTTON,
    TS_ADD_TO_CART,
    TS_COOKIE_ACCEPT,
    NAV_TIMEOUT_MS,
    ACTION_DELAY_SEC,
    LOGIN_WAIT_SEC,
    PAGE_LOAD_WAIT_SEC,
)

logger = logging.getLogger(__name__)

BROWSER_STATE_PATH = Path(__file__).parent / "browser_state.json"

Store = Literal["beatport", "traxsource"]

_running: dict[str, bool] = {"beatport": False, "traxsource": False}


def is_running(store: Store) -> bool:
    """Check whether a cart-build session is already active for *store*."""
    return _running.get(store, False)


# ---------------------------------------------------------------------------
# Async WebSocket helper (safe to call from sync thread)
# ---------------------------------------------------------------------------

def _broadcast(event_type: str, payload: Any) -> None:
    """Fire-and-forget a WebSocket broadcast from the sync Playwright thread."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(manager.broadcast(event_type, payload))
        else:
            loop.run_until_complete(manager.broadcast(event_type, payload))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(manager.broadcast(event_type, payload))
        loop.close()


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

def _beatport_login(page: Page) -> bool:
    """Log in to Beatport using .env credentials. Returns True on success."""
    email = os.environ.get("BEATPORT_EMAIL", "")
    password = os.environ.get("BEATPORT_PASSWORD", "")
    if not email or not password:
        logger.error("BEATPORT_EMAIL / BEATPORT_PASSWORD not set in .env")
        return False

    page.goto(BEATPORT_LOGIN_URL, timeout=NAV_TIMEOUT_MS)
    time.sleep(PAGE_LOAD_WAIT_SEC)
    _dismiss_cookie_banner(page, BP_COOKIE_ACCEPT)

    page.locator(BP_EMAIL_INPUT).first.fill(email)
    page.locator(BP_PASSWORD_INPUT).first.fill(password)
    page.locator(BP_LOGIN_BUTTON).first.click()
    time.sleep(LOGIN_WAIT_SEC)

    if page.locator(BP_LOGGED_IN_INDICATOR).first.is_visible(timeout=5_000):
        logger.info("Beatport login successful")
        return True

    logger.warning("Beatport login may have failed — indicator not found")
    return False


def _beatport_is_logged_in(page: Page) -> bool:
    """Check if we already have a valid Beatport session."""
    page.goto(BEATPORT_BASE_URL, timeout=NAV_TIMEOUT_MS)
    time.sleep(PAGE_LOAD_WAIT_SEC)
    # #region agent log
    _dlog("cart_builder.py:is_logged_in", "Navigated to Beatport", {"url": page.url}, "H3")
    # #endregion
    _dismiss_cookie_banner(page, BP_COOKIE_ACCEPT)
    try:
        result = page.locator(BP_LOGGED_IN_INDICATOR).first.is_visible(timeout=5_000)
        # #region agent log
        _dlog("cart_builder.py:is_logged_in", "Login check result", {"is_logged_in": result, "url": page.url}, "H3")
        # #endregion
        return result
    except PlaywrightTimeout:
        # #region agent log
        _dlog("cart_builder.py:is_logged_in", "Login check TIMED OUT", {}, "H3")
        # #endregion
        return False


def _beatport_add_track(page: Page, track: dict[str, Any]) -> bool:
    """Navigate to a Beatport track URL, select WAV, add to cart."""
    url = track.get("beatport_url")
    if not url:
        logger.warning("Track %s has no beatport_url — skipping", track["id"])
        return False

    page.goto(url, timeout=NAV_TIMEOUT_MS)
    time.sleep(PAGE_LOAD_WAIT_SEC)
    # #region agent log
    _dlog("cart_builder.py:add_track_nav", "Track page loaded", {"url": page.url, "track_id": track["id"]}, "H4,H5")
    # #endregion

    try:
        dropdown = page.locator(BP_FORMAT_DROPDOWN).first
        fmt_visible = dropdown.is_visible(timeout=3_000)
        # #region agent log
        _dlog("cart_builder.py:add_track_fmt", "Format dropdown check", {"visible": fmt_visible}, "H2")
        # #endregion
        if fmt_visible:
            dropdown.click()
            time.sleep(0.5)
            page.locator(BP_WAV_OPTION).first.click()
            time.sleep(0.5)
    except PlaywrightTimeout:
        # #region agent log
        _dlog("cart_builder.py:add_track_fmt", "Format dropdown NOT found", {}, "H2")
        # #endregion
        logger.debug("No format dropdown found for %s — may default to WAV", url)

    try:
        cart_btn = page.locator(BP_ADD_TO_CART).first
        # #region agent log
        btn_count = page.locator(BP_ADD_TO_CART).count()
        _dlog("cart_builder.py:add_track_cart", "Looking for Add-to-Cart button", {"selector": BP_ADD_TO_CART, "match_count": btn_count}, "H1")
        # #endregion
        cart_btn.wait_for(state="visible", timeout=10_000)
        cart_btn.click()
        # #region agent log
        _dlog("cart_builder.py:add_track_cart", "Add-to-Cart CLICKED", {"track_id": track["id"]}, "H1")
        # #endregion
        time.sleep(ACTION_DELAY_SEC)
        return True
    except PlaywrightTimeout:
        # #region agent log
        _dlog("cart_builder.py:add_track_cart", "Add-to-Cart TIMEOUT — button not found", {"url": page.url, "track_id": track["id"], "selector": BP_ADD_TO_CART}, "H1")
        # #endregion
        logger.error("Add-to-cart button not found on %s", url)
        return False


# ---------------------------------------------------------------------------
# Traxsource automation
# ---------------------------------------------------------------------------

def _traxsource_login(page: Page) -> bool:
    """Log in to Traxsource using .env credentials."""
    email = os.environ.get("TRAXSOURCE_EMAIL", "")
    password = os.environ.get("TRAXSOURCE_PASSWORD", "")
    if not email or not password:
        logger.error("TRAXSOURCE_EMAIL / TRAXSOURCE_PASSWORD not set in .env")
        return False

    page.goto(TRAXSOURCE_LOGIN_URL, timeout=NAV_TIMEOUT_MS)
    time.sleep(PAGE_LOAD_WAIT_SEC)
    _dismiss_cookie_banner(page, TS_COOKIE_ACCEPT)

    page.locator(TS_EMAIL_INPUT).first.fill(email)
    page.locator(TS_PASSWORD_INPUT).first.fill(password)
    page.locator(TS_LOGIN_BUTTON).first.click()
    time.sleep(LOGIN_WAIT_SEC)

    if page.locator(TS_LOGGED_IN_INDICATOR).first.is_visible(timeout=5_000):
        logger.info("Traxsource login successful")
        return True

    logger.warning("Traxsource login may have failed — indicator not found")
    return False


def _traxsource_is_logged_in(page: Page) -> bool:
    """Check if we already have a valid Traxsource session."""
    page.goto(TRAXSOURCE_BASE_URL, timeout=NAV_TIMEOUT_MS)
    time.sleep(PAGE_LOAD_WAIT_SEC)
    _dismiss_cookie_banner(page, TS_COOKIE_ACCEPT)
    try:
        return page.locator(TS_LOGGED_IN_INDICATOR).first.is_visible(timeout=5_000)
    except PlaywrightTimeout:
        return False


def _traxsource_add_track(page: Page, track: dict[str, Any]) -> bool:
    """Navigate to a Traxsource track URL, select WAV, add to cart."""
    url = track.get("traxsource_url")
    if not url:
        logger.warning("Track %s has no traxsource_url — skipping", track["id"])
        return False

    page.goto(url, timeout=NAV_TIMEOUT_MS)
    time.sleep(PAGE_LOAD_WAIT_SEC)

    try:
        wav_btn = page.locator(TS_WAV_BUY_BUTTON).first
        if wav_btn.is_visible(timeout=3_000):
            wav_btn.click()
            time.sleep(0.5)
    except PlaywrightTimeout:
        logger.debug("No WAV button found for %s — trying direct add", url)

    try:
        cart_btn = page.locator(TS_ADD_TO_CART).first
        cart_btn.wait_for(state="visible", timeout=5_000)
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

    url_field = "beatport_url" if store == "beatport" else "traxsource_url"
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

                # Save session cookies for next run
                context.storage_state(path=str(BROWSER_STATE_PATH))
                logger.info("Browser state saved to %s", BROWSER_STATE_PATH)

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
        # #region agent log
        _dlog("cart_builder.py:build_exception", "Cart build EXCEPTION", {"store": store, "error_type": type(exc).__name__, "error_str": str(exc)[:500]}, "H1,H3")
        # #endregion
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
    """Launch headless Chromium, restoring session cookies if available."""
    browser: Browser = pw.chromium.launch(headless=True)

    storage = str(BROWSER_STATE_PATH) if BROWSER_STATE_PATH.exists() else None
    context = browser.new_context(
        storage_state=storage,
        viewport={"width": 1280, "height": 900},
    )
    context.set_default_timeout(NAV_TIMEOUT_MS)
    page = context.new_page()
    return context, page


def _ensure_logged_in(page: Page, store: Store) -> None:
    """Check session validity; log in if needed."""
    if store == "beatport":
        if not _beatport_is_logged_in(page):
            if not _beatport_login(page):
                raise RuntimeError("Failed to log in to Beatport")
    else:
        if not _traxsource_is_logged_in(page):
            if not _traxsource_login(page):
                raise RuntimeError("Failed to log in to Traxsource")


def _add_track_to_cart(page: Page, track: dict[str, Any], store: Store) -> bool:
    """Dispatch to the correct store's add-to-cart routine."""
    try:
        if store == "beatport":
            return _beatport_add_track(page, track)
        return _traxsource_add_track(page, track)
    except Exception as exc:
        logger.error(
            "Exception adding track %s to %s cart: %s",
            track["id"], store, exc,
        )
        return False


def _navigate_to_cart(page: Page, store: Store) -> None:
    """Go to the cart / checkout page so the user can review."""
    cart_url = BEATPORT_CART_URL if store == "beatport" else TRAXSOURCE_CART_URL
    try:
        page.goto(cart_url, timeout=NAV_TIMEOUT_MS)
        time.sleep(PAGE_LOAD_WAIT_SEC)
    except PlaywrightTimeout:
        logger.warning("Timed out navigating to %s cart page", store)
