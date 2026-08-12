"""Cart BP journeys: login selector, desktop Chrome UA, and UI-visible errors."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import cart_builder
from store_selectors import BP_LOGIN_TRIGGER


def test_beatport_login_trigger_finds_log_in_by_visible_text():
    """Beatport login looks for a Log In control by visible text, not a hashed CSS class."""
    assert "f1d733ca" not in BP_LOGIN_TRIGGER
    finds_log_in_by_text = (
        'has-text("Log In")' in BP_LOGIN_TRIGGER
        or "has-text('Log In')" in BP_LOGIN_TRIGGER
        or 'text="Log In"' in BP_LOGIN_TRIGGER
        or "text='Log In'" in BP_LOGIN_TRIGGER
        or "text=Log In" in BP_LOGIN_TRIGGER
    )
    assert finds_log_in_by_text, (
        "BP_LOGIN_TRIGGER must locate Log In by visible text "
        f"(for example a:has-text(\"Log In\")); got {BP_LOGIN_TRIGGER!r}"
    )
    scoped_to_control = "a:" in BP_LOGIN_TRIGGER or "button:" in BP_LOGIN_TRIGGER
    assert scoped_to_control, (
        "Log In text selector must target an a or button control, "
        f"got {BP_LOGIN_TRIGGER!r}"
    )


def test_headless_browser_sends_a_desktop_chrome_user_agent():
    """Chromium stays headless but must look like desktop Chrome so Cloudflare does not show Just a moment."""
    pw = MagicMock()
    browser = MagicMock()
    context = MagicMock()
    page = MagicMock()
    pw.chromium.launch.return_value = browser
    browser.new_context.return_value = context
    context.new_page.return_value = page

    returned_context, returned_page = cart_builder._launch_browser(pw, "beatport")

    assert returned_context is context
    assert returned_page is page
    launch_kwargs = pw.chromium.launch.call_args.kwargs
    assert launch_kwargs.get("headless") is True

    context_kwargs = browser.new_context.call_args.kwargs
    user_agent = context_kwargs.get("user_agent") or ""
    assert user_agent, (
        "new_context must set a desktop Chrome user_agent; "
        "Cloudflare treats the default HeadlessChrome fingerprint as a bot"
    )
    assert "HeadlessChrome" not in user_agent
    assert "Mozilla/5.0" in user_agent
    assert "Chrome/" in user_agent


def test_cart_error_from_a_worker_thread_shows_up_on_the_app_loop():
    """Clicking Cart BP after Beatport login fails must put cart_error on the FastAPI loop."""
    app_loop = asyncio.new_event_loop()
    loop_ready = threading.Event()

    def run_app_loop() -> None:
        asyncio.set_event_loop(app_loop)
        loop_ready.set()
        app_loop.run_forever()

    app_thread = threading.Thread(target=run_app_loop, daemon=True, name="app-loop")
    app_thread.start()
    assert loop_ready.wait(timeout=5)

    delivered: list[tuple[str, object, asyncio.AbstractEventLoop]] = []

    async def capture_broadcast(event_type: str, payload: object = None) -> None:
        delivered.append((event_type, payload, asyncio.get_running_loop()))

    scheduled_loops: list[asyncio.AbstractEventLoop] = []
    real_rcts = asyncio.run_coroutine_threadsafe

    def track_run_coroutine_threadsafe(coro, loop):
        scheduled_loops.append(loop)
        return real_rcts(coro, loop)

    # Same contract as file_pipeline: FastAPI captures its running loop on
    # cart_builder._loop before asyncio.to_thread, so the Playwright worker
    # can schedule broadcasts onto it.
    cart_builder._loop = app_loop
    worker_done = threading.Event()
    worker_errors: list[BaseException] = []
    payload = {"store": "beatport", "error": "Failed to log in to Beatport"}

    try:
        with (
            patch.object(cart_builder.manager, "broadcast", new=capture_broadcast),
            patch.object(
                cart_builder.asyncio,
                "run_coroutine_threadsafe",
                new=track_run_coroutine_threadsafe,
            ),
        ):
            def playwright_worker() -> None:
                try:
                    cart_builder._broadcast("cart_error", payload)
                except BaseException as exc:
                    worker_errors.append(exc)
                finally:
                    worker_done.set()

            threading.Thread(
                target=playwright_worker, daemon=True, name="playwright-worker"
            ).start()
            assert worker_done.wait(timeout=5)
            assert worker_errors == []

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not delivered:
                time.sleep(0.05)

            assert scheduled_loops, (
                "cart_error must be scheduled with asyncio.run_coroutine_threadsafe "
                "on the captured app loop; _broadcast currently uses "
                "asyncio.get_event_loop() from the Playwright worker thread"
            )
            assert scheduled_loops[0] is app_loop
            assert delivered, "cart_error was never delivered on the app loop"
            event_type, event_payload, running_loop = delivered[0]
            assert event_type == "cart_error"
            assert event_payload == payload
            assert running_loop is app_loop
    finally:
        cart_builder._loop = None
        app_loop.call_soon_threadsafe(app_loop.stop)
        app_thread.join(timeout=5)
