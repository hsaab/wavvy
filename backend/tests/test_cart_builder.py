"""Cart BP journeys: login selector, headed stealth launch, human-check wait, UI errors."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from unittest.mock import MagicMock, patch

from playwright.sync_api import TimeoutError as PlaywrightTimeout

import cart_builder
from store_selectors import (
    BP_LOGGED_IN_INDICATOR,
    BP_LOGIN_TRIGGER,
    NAV_TIMEOUT_MS,
)


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


def _init_script_sources(*mocks: MagicMock) -> list[str]:
    """Collect inline scripts passed to Playwright add_init_script."""
    scripts: list[str] = []
    for mock_obj in mocks:
        for call in mock_obj.add_init_script.call_args_list:
            if call.args:
                scripts.append(str(call.args[0]))
            script = call.kwargs.get("script")
            if script:
                scripts.append(str(script))
    return scripts


def test_clicking_cart_bp_launches_headed_chrome_without_bot_fingerprints():
    """Cart BP opens headed Chromium that looks like desktop Chrome, not HeadlessChrome or webdriver."""
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
    assert launch_kwargs.get("headless") is False, (
        "Cart BP must launch headed Chromium (headless=False); "
        "headless Chrome is fingerprinted as HeadlessChrome and Cloudflare shows Just a moment"
    )
    launch_args = [str(arg) for arg in (launch_kwargs.get("args") or [])]
    assert any("disable-blink-features=AutomationControlled" in arg for arg in launch_args), (
        "chromium.launch args must include --disable-blink-features=AutomationControlled; "
        f"got {launch_args!r}"
    )

    context_kwargs = browser.new_context.call_args.kwargs
    user_agent = context_kwargs.get("user_agent") or ""
    assert user_agent, (
        "new_context must set a desktop Chrome user_agent; "
        "Cloudflare treats the default HeadlessChrome fingerprint as a bot"
    )
    assert "HeadlessChrome" not in user_agent
    assert "Mozilla/5.0" in user_agent
    assert "Chrome/" in user_agent

    scripts = _init_script_sources(context, page)
    assert scripts, (
        "must call add_init_script on the context (or page) so navigator.webdriver is not advertised"
    )
    combined = "\n".join(scripts)
    assert "webdriver" in combined, (
        "init script must hide navigator.webdriver; "
        f"got {combined!r}"
    )


def _is_log_in_or_avatar_selector(selector: str) -> bool:
    text = str(selector)
    return (
        "Log In" in text
        or "account_avatar" in text
        or text == BP_LOGIN_TRIGGER
        or text == BP_LOGGED_IN_INDICATOR
    )


def _cloudflare_challenge_page() -> tuple[MagicMock, list[int]]:
    """A Beatport page stuck on Just a moment, with Log In and avatar never visible."""
    homepage_timeouts: list[int] = []
    page = MagicMock()
    page.title.return_value = "Just a moment..."
    page.content.return_value = "Verify you are human"
    page.goto.return_value = None

    def make_locator(selector: str = "") -> MagicMock:
        loc = MagicMock()
        loc.first = loc

        def record(timeout: int | None) -> None:
            if _is_log_in_or_avatar_selector(selector) and timeout is not None:
                homepage_timeouts.append(timeout)

        def click(timeout: int | None = None, **_kwargs: object) -> None:
            record(timeout)
            raise PlaywrightTimeout("Timeout")

        def wait_for(state: str | None = None, timeout: int | None = None, **_kwargs: object) -> None:
            record(timeout)
            raise PlaywrightTimeout("Timeout")

        def is_visible(timeout: int | None = None, **_kwargs: object) -> bool:
            record(timeout)
            return False

        loc.click.side_effect = click
        loc.wait_for.side_effect = wait_for
        loc.is_visible.side_effect = is_visible
        loc.or_.side_effect = lambda _other: make_locator(f"{selector} or avatar")
        return loc

    def wait_for_selector(selector: str, timeout: int | None = None, **_kwargs: object) -> None:
        if _is_log_in_or_avatar_selector(selector) and timeout is not None:
            homepage_timeouts.append(timeout)
        raise PlaywrightTimeout("Timeout")

    page.locator.side_effect = lambda selector, **_kw: make_locator(selector)
    page.wait_for_selector.side_effect = wait_for_selector

    def wait_for_function(_expr: object, timeout: int | None = None, **_kwargs: object) -> None:
        if timeout is not None:
            homepage_timeouts.append(timeout)
        raise PlaywrightTimeout("Timeout")

    page.wait_for_function.side_effect = wait_for_function
    return page, homepage_timeouts


def test_human_check_page_waits_for_log_in_and_names_the_challenge():
    """If Beatport stays on Just a moment, login waits NAV_TIMEOUT_MS and names the human check."""
    page, homepage_timeouts = _cloudflare_challenge_page()
    logged: list[str] = []

    def capture_log(msg: object, *args: object, **_kwargs: object) -> None:
        logged.append(msg % args if args else str(msg))

    env = {
        "BEATPORT_EMAIL": "dj@example.com",
        "BEATPORT_PASSWORD": "secret",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch.object(cart_builder.time, "sleep"),
        patch.object(cart_builder.logger, "error", side_effect=capture_log),
        patch.object(cart_builder.logger, "warning", side_effect=capture_log),
    ):
        try:
            cart_builder._beatport_login(page)
        except Exception as exc:
            logged.append(str(exc))

    waited_for_homepage = any(timeout >= NAV_TIMEOUT_MS for timeout in homepage_timeouts)
    failure_text = " ".join(logged).lower()
    names_human_check = (
        "human" in failure_text
        or "just a moment" in failure_text
        or "cloudflare" in failure_text
    )
    assert waited_for_homepage and names_human_check, (
        "login must wait NAV_TIMEOUT_MS for Log In or the avatar (not a 5s click) "
        "and the failure must name the human check, not only "
        "'Login trigger not found on Beatport homepage'; "
        f"got timeouts {homepage_timeouts!r} and {logged!r}"
    )


def test_cart_bp_loads_beatport_homepage_only_once():
    """Cart BP must not goto beatport.com a second time after the human check, which restarts Cloudflare."""
    page = MagicMock()
    page.url = ""
    page.title.return_value = "Beatport | DJ & Electronic Dance Music"
    page.content.return_value = "Log In"

    def make_locator(selector: str = "") -> MagicMock:
        loc = MagicMock()
        loc.first = loc
        text = str(selector)
        is_login = "Log In" in text or text == BP_LOGIN_TRIGGER
        is_avatar = "account_avatar" in text or text == BP_LOGGED_IN_INDICATOR
        loc.is_visible.return_value = is_login and not is_avatar
        loc.wait_for.return_value = None
        loc.click.return_value = None
        loc.fill.return_value = None
        loc.or_.side_effect = lambda _other: make_locator("Log In or avatar")
        return loc

    page.locator.side_effect = lambda selector, **_kw: make_locator(selector)
    page.wait_for_function.return_value = None
    page.wait_for_url.side_effect = PlaywrightTimeout("Timeout")

    env = {"BEATPORT_EMAIL": "dj@example.com", "BEATPORT_PASSWORD": "secret"}
    with (
        patch.dict(os.environ, env, clear=False),
        patch.object(cart_builder.time, "sleep"),
    ):
        try:
            cart_builder._ensure_logged_in(page, "beatport")
        except RuntimeError:
            pass

    goto_urls = [str(call.args[0]) for call in page.goto.call_args_list if call.args]
    homepage_loads = [
        url for url in goto_urls if str(url).rstrip("/") == "https://www.beatport.com"
    ]
    assert len(homepage_loads) == 1, (
        "loading www.beatport.com twice restarts the Cloudflare human check; "
        f"got gotos {goto_urls!r}"
    )


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
