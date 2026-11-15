"""The browser login: the one place a password is typed, and the session it leaves.

`browser_login` serves manual login and explicitly enabled automatic
renewal. Chromium loads only when needed; browser authentication is separate
from the HTTP source client's read-only boundary.
"""
from __future__ import annotations

import asyncio
import stat
import sys
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

from skolekit.auth.credentials import default_credentials
from skolekit.auth.session import SessionStore
from skolekit.state import (
    ensure_private_dir,
    state_dir,
    write_private_json,
)
from skolekit.transport import (
    is_allowed_url,
    is_authenticated_page,
    is_login_url,
    require_allowed_url,
    sanitize_url,
)


async def first_page(context: BrowserContext) -> Page:
    if context.pages:
        return context.pages[0]
    return await context.new_page()

async def open_context(
    *, headless: bool, persistent_profile: bool = False
) -> tuple[Any, BrowserContext]:
    from playwright.async_api import async_playwright

    runner = await async_playwright().start()
    context_options: dict[str, Any] = {
        "viewport": {"width": 1280, "height": 900},
        "locale": "da-DK",
        "timezone_id": "Europe/Copenhagen",
    }
    if persistent_profile:
        profile_dir = ensure_private_dir(state_dir() / "browser-profile")
        context = await runner.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            **context_options,
        )
    else:
        browser = await runner.chromium.launch(headless=headless)
        state_path = SessionStore().path
        if state_path.exists():
            context_options["storage_state"] = str(state_path)
        context = await browser.new_context(**context_options)
    return runner, context

async def close_context(runner: Any, context: BrowserContext) -> None:
    await context.close()
    await runner.stop()

async def prefill_login_fields(
    context: BrowserContext,
    username: str,
    password: str,
    deadline: float,
    *,
    auto_submit: bool = False,
) -> None:
    """Fill a verified SkoleIntra login form and optionally submit it once."""
    filled_username = False
    filled_password = False
    while monotonic() < deadline and not (filled_username and filled_password):
        for page in context.pages:
            for frame in page.frames:
                if not (is_allowed_url(frame.url) and is_login_url(frame.url)):
                    continue
                forms = frame.locator("form")
                if await forms.count() != 1:
                    continue
                form = forms.first
                username_candidates = form.locator(
                    'input[name="UserName"], input[autocomplete="username"], '
                    'input[type="email"], input[name*="user" i]'
                )
                password_candidates = form.locator(
                    'input[name="Password"], input[type="password"]'
                )
                if (
                    await username_candidates.count() != 1
                    or await password_candidates.count() != 1
                ):
                    continue
                username_field = username_candidates.first
                password_field = password_candidates.first
                if not (
                    await username_field.is_visible()
                    and await password_field.is_visible()
                ):
                    continue
                if not filled_username:
                    await username_field.fill(username)
                    filled_username = True
                    print("Username filled from macOS Keychain.")
                if not filled_password:
                    await password_field.fill(password)
                    filled_password = True
                    if auto_submit:
                        submit = form.locator(
                            'input[type="submit"], button[type="submit"]'
                        )
                        if await submit.count() != 1:
                            raise ValueError(
                                "Verified login form did not have one submit control"
                            )
                        print("Submitting the verified SkoleIntra login form.")
                        await submit.first.click()
                    else:
                        print(
                            "Password prefilled from macOS Keychain; submit manually."
                        )
                    return
        await asyncio.sleep(0.5)

async def browser_login(url: str, *, timeout: int = 15, use_keychain: bool = False,
                        auto_submit: bool = False, headless: bool = False) -> int:
    """Sign in in a private browser and save the session; `timeout` is in minutes."""
    target = require_allowed_url(url)
    if auto_submit and not use_keychain:
        print("--auto-submit requires --use-keychain.", file=sys.stderr)
        return 2
    if headless and not auto_submit:
        print("--headless requires --auto-submit.", file=sys.stderr)
        return 2
    credentials = default_credentials().school_login() if use_keychain else None
    if use_keychain and credentials is None:
        print(
            "No school login is saved in the Keychain; save it first (`skolekit auth credentials set`).",
            file=sys.stderr,
        )
        return 2
    runner, context = await open_context(
        headless=headless, persistent_profile=True
    )
    try:
        page = await first_page(context)
        await page.goto(target, wait_until="domcontentloaded")
        if credentials:
            if auto_submit:
                print(
                    "Keychain credentials will be submitted only to a verified "
                    "SkoleIntra login form."
                )
            else:
                print("Keychain credentials will be prefilled when matching fields appear.")
                print("You remain responsible for submitting the form and completing MFA.")
        else:
            print("Complete the SkoleIntra login in the browser window.")
            print("You can use the macOS Passwords app or paste credentials manually.")
        print(f"Waiting up to {timeout} minutes for an authenticated page …")

        deadline = monotonic() + timeout * 60
        prefill_task = (
            asyncio.create_task(
                prefill_login_fields(context, credentials[0], credentials[1], deadline)
                if not auto_submit
                else prefill_login_fields(
                    context,
                    credentials[0],
                    credentials[1],
                    deadline,
                    auto_submit=True,
                )
            )
            if credentials
            else None
        )
        authenticated_page: Page | None = None
        candidate_page: Page | None = None
        candidate_url: str | None = None
        candidate_since: float | None = None
        while monotonic() < deadline:
            current_candidate: Page | None = None
            for candidate in context.pages:
                title = (await candidate.title()).lower()
                if is_authenticated_page(candidate.url) and "login" not in title:
                    current_candidate = candidate
                    break
            if current_candidate is None:
                candidate_page = None
                candidate_url = None
                candidate_since = None
            elif (
                current_candidate is not candidate_page
                or current_candidate.url != candidate_url
            ):
                candidate_page = current_candidate
                candidate_url = current_candidate.url
                candidate_since = monotonic()
            elif candidate_since is not None and monotonic() - candidate_since >= 3:
                authenticated_page = current_candidate
                break
            await asyncio.sleep(0.5)

        if authenticated_page is None:
            if prefill_task:
                prefill_task.cancel()
            print("Login was not completed before the timeout.", file=sys.stderr)
            return 2

        if prefill_task:
            prefill_task.cancel()
        await authenticated_page.wait_for_timeout(1500)
        state_path = SessionStore().path
        ensure_private_dir(state_path.parent)
        await context.storage_state(path=str(state_path))
        state_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        metadata = {
            "authenticated_at": datetime.now(UTC).isoformat(),
            "url": sanitize_url(authenticated_page.url),
        }
        write_private_json(state_dir() / "session.json", metadata)
        print("Authenticated session saved in the private browser profile.")
        return 0
    finally:
        await close_context(runner, context)
