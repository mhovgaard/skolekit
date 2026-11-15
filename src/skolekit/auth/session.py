"""The saved school session, and the read-only client that uses it.

`SessionStore` keeps private login cookies in `auth-state.json`.
`school_client` reuses them through the configured school's read-only client.
"""
from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from skolekit.state import state_dir
from skolekit.transport import (
    ReadOnlyClient,
    SessionExpired,
    is_authenticated_page,
    is_school_host,
    require_allowed_url,
)

SESSION_FILE = "auth-state.json"


class SessionStore:
    """`auth-state.json` in the state directory: the cookies a login left behind."""

    def __init__(self, home: Path | None = None) -> None:
        self._home = home

    @property
    def path(self) -> Path:
        return (self._home or state_dir()) / SESSION_FILE

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: Mapping[str, Any]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(stat.S_IRWXU)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary.replace(path)


def cookies_from_state(state: Mapping[str, Any]) -> httpx.Cookies:
    """The session's SkoleIntra cookies; any other site's are left out."""
    jar = httpx.Cookies()
    for cookie in state.get("cookies", []):
        domain = cookie.get("domain")
        if not domain or not is_school_host(domain.lstrip(".")):
            continue
        jar.set(cookie["name"], cookie["value"], domain=domain, path=cookie.get("path", "/"))
    return jar


def school_client(state: Mapping[str, Any], host: str, *,
                  transport: httpx.BaseTransport | None = None) -> ReadOnlyClient:
    """The read-only client for the school's `host`, signed in with the saved session."""
    return ReadOnlyClient(host, cookies=cookies_from_state(state), transport=transport)


def session_is_valid(state: Mapping[str, Any], url: str) -> bool:
    try:
        with school_client(state, urlparse(require_allowed_url(url)).hostname or "") as client:
            response = client.get(url)
        return response.is_success and is_authenticated_page(str(response.url))
    except (httpx.HTTPError, SessionExpired):
        return False
