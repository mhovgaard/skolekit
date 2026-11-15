"""Command helpers for common."""
from __future__ import annotations

import argparse
import html
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from skoleklar.household import snapshot_db_path
from skolekit.transport import require_allowed_url
from skolekit.auth.session import SessionStore
from skolekit.state import state_dir
from skoleklar.household import load_site_profile
from skolekit.profile import ProfileError


def config_root() -> Path:
    return state_dir()


def target_url(args: argparse.Namespace, *, page: str = "login") -> str:
    """The --url given, else the profile's login page (or its inbox); always on the school's host."""
    profile = load_site_profile()
    url = getattr(args, "url", None) or (
        profile.messages_url if page == "messages" else profile.login_url
    )
    require_allowed_url(url)
    if (urlparse(url).hostname or "").lower() != profile.school.host:
        raise ValueError(f"--url must be on the school's host, {profile.school.host}")
    return url


def _host(url: str) -> str:
    return urlparse(url).hostname or ""


def load_auth_state() -> dict[str, Any] | None:
    return SessionStore().load()


def json_shape(value: Any, depth: int = 0) -> Any:
    """Return structure and types without retaining private response values."""
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, Mapping):
        return {
            str(key): json_shape(child, depth + 1)
            for key, child in list(value.items())[:80]
        }
    if isinstance(value, list):
        sample = [json_shape(child, depth + 1) for child in value[:3]]
        return {"type": "list", "length": len(value), "sample_shapes": sample}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def html_title(body: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.I | re.S)
    if not match:
        return None
    return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()


def _runtime_config_or_report():
    from skoleklar.runtime.config import load_runtime_config
    try:
        return load_runtime_config(config_root())
    except ProfileError as error:
        print(f"Invalid {error}", file=sys.stderr)
        return None


def _one_writer(handler):
    """`handler`, refused while skoleklar.toml says the cloud writes the household's database.

    Only a write to the household's own database is refused: a dry run, a brief
    that records nothing, or a command pointed at another file with --db runs.
    """
    def guarded(args: argparse.Namespace):
        if getattr(args, "dry_run", False) or (getattr(args, "command", None) == "brief" and not getattr(args, "record_delivery", False)):
            return handler(args)
        target = getattr(args, "db", None)
        if target and Path(target).expanduser().resolve() != snapshot_db_path().resolve():
            return handler(args)
        config = _runtime_config_or_report()
        if config is None:
            return 2
        if config.where == "gcp":
            print(f"skoleklar.toml says the household's database is written by its gcp runtime; "
                  f"`skoleklar {getattr(args, 'command', '')}` would make a second writer, so it stops. "
                  "Read a pulled copy instead (`skoleklar db pull`, then --db).", file=sys.stderr)
            return 2
        return handler(args)
    guarded.__wrapped__ = handler
    return guarded


def load_profile_or_report():
    try:
        return load_site_profile()
    except ProfileError as error:
        print(f"Invalid profile: {error}", file=sys.stderr)
        return None
