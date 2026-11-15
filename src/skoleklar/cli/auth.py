"""Command helpers for auth."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any
from skolekit.auth.credentials import KeychainCredentials, default_credentials
from skoleklar.settings import KEYCHAIN_SERVICE, OPENROUTER_KEYCHAIN_ACCOUNT, openrouter_api_key
from skolekit.auth.browser import browser_login, close_context, first_page, open_context
from skolekit.transport import sanitize_url
from skolekit.auth.session import school_client
from skoleklar.household import load_site_profile
from skolekit.transport import SessionExpired, is_authenticated_page
from skolekit.parsers.weekly_plan import parse_weekly_plan_html, weekly_plan_metadata
from skoleklar.cli.common import _host, html_title, json_shape, load_auth_state, target_url


def load_keychain_credentials() -> tuple[str, str] | None:
    return default_credentials().school_login()


def command_credentials_set(_: argparse.Namespace) -> int:
    username = input("SkoleIntra username: ").strip()
    if not username:
        print("Username cannot be empty.", file=sys.stderr)
        return 2
    password = getpass.getpass("SkoleIntra password: ")
    if not password:
        print("Password cannot be empty.", file=sys.stderr)
        return 2
    KeychainCredentials().save(username, password)
    print("Credentials saved in macOS Keychain.")
    return 0


def command_credentials_status(_: argparse.Namespace) -> int:
    configured = load_keychain_credentials() is not None
    print(json.dumps({"keychain_credentials": configured}))
    return 0 if configured else 2


def command_credentials_delete(_: argparse.Namespace) -> int:
    KeychainCredentials().delete()
    print("Stored SkoleIntra credentials removed from macOS Keychain.")
    return 0


def load_openrouter_api_key() -> str | None:
    return openrouter_api_key()


def command_llm_credentials_set(_: argparse.Namespace) -> int:
    import keyring

    api_key = getpass.getpass("OpenRouter API key: ").strip()
    if not api_key:
        print("API key cannot be empty.", file=sys.stderr)
        return 2
    keyring.set_password(KEYCHAIN_SERVICE, OPENROUTER_KEYCHAIN_ACCOUNT, api_key)
    print("OpenRouter API key saved in macOS Keychain.")
    return 0


def command_llm_credentials_status(_: argparse.Namespace) -> int:
    source = "environment" if os.environ.get("OPENROUTER_API_KEY") else None
    if source is None:
        import keyring

        if keyring.get_password(KEYCHAIN_SERVICE, OPENROUTER_KEYCHAIN_ACCOUNT):
            source = "keychain"
    print(json.dumps({"configured": source is not None, "source": source}))
    return 0 if source is not None else 2


def command_llm_credentials_delete(_: argparse.Namespace) -> int:
    import keyring

    try:
        keyring.delete_password(KEYCHAIN_SERVICE, OPENROUTER_KEYCHAIN_ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass
    print("Stored OpenRouter API key removed from macOS Keychain.")
    return 0


async def command_check(args: argparse.Namespace) -> int:
    target = target_url(args)
    runner, context = await open_context(headless=True)
    try:
        page = await first_page(context)
        response = await page.goto(target, wait_until="domcontentloaded")
        result = {
            "authenticated": is_authenticated_page(page.url),
            "final_url": sanitize_url(page.url),
            "status": response.status if response else None,
            "title": await page.title(),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["authenticated"] else 2
    finally:
        await close_context(runner, context)


def command_probe(args: argparse.Namespace) -> int:
    target = target_url(args)
    state = load_auth_state()
    if state is None:
        print("No saved session. Run `uv run skoleklar login` first.", file=sys.stderr)
        return 2
    try:
        with school_client(state, _host(target)) as client:
            response = client.get(target)
    except SessionExpired:
        print(json.dumps({"authenticated": False, "error": "session_expired"}, indent=2))
        return 2

    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    result: dict[str, Any] = {
        "authenticated": is_authenticated_page(str(response.url)),
        "status": response.status_code,
        "final_url": sanitize_url(str(response.url)),
        "content_type": content_type,
        "content_length": len(response.content),
    }
    if content_type == "application/json":
        try:
            result["json_shape"] = json_shape(response.json())
        except ValueError:
            result["json_shape"] = "unavailable"
    elif content_type == "text/html":
        result["title"] = html_title(response.text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["authenticated"] and response.is_success else 2


def command_weekly_plan(args: argparse.Namespace) -> int:
    target = target_url(args)
    state = load_auth_state()
    if state is None:
        print("No saved session. Run `uv run skoleklar login` first.", file=sys.stderr)
        return 2
    try:
        with school_client(state, _host(target)) as client:
            response = client.get(target)
    except SessionExpired:
        response = None
    if response is None or not response.is_success:
        print("Weekly-plan request was not authenticated.", file=sys.stderr)
        return 2
    plan = parse_weekly_plan_html(response.text, str(response.url),
                                  wording=load_site_profile().school.wording)
    output = plan if args.include_content else weekly_plan_metadata(plan)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


async def command_login(args: argparse.Namespace) -> int:
    return await browser_login(
        target_url(args),
        timeout=args.timeout,
        use_keychain=args.use_keychain,
        auto_submit=args.auto_submit,
        headless=args.headless,
    )
