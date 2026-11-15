"""The one way to the school: a read-only HTTP client.

Every request to SkoleIntra goes through `ReadOnlyClient`:

- it reaches only the profile's own host, over HTTPS;
- it sends GET (and HEAD), plus POST only to known read endpoints, today the
  front page's `newsitems/showmore`;
- a redirect away from the host, or to a login page, raises `SessionExpired`,
  one error for every source;
- it sets the language and user-agent headers once.

A test fails if a module builds its own client for the school
(tests_python/test_transport.py). Clients for other services, such as the model
provider or Cloud Run, are listed there.

"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

ALLOWED_HOST_SUFFIX = ".skoleintra.dk"
# POST paths that only read: the front page's "show more" fetches the next items.
READ_POSTS = frozenset({"newsitems/showmore"})
HEADERS = {
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.7",
    "User-Agent": "skolekit/0.1 (read-only)",
}
_READ_METHODS = frozenset({"GET", "HEAD"})


class ReadOnlyViolation(RuntimeError):
    """A request the read-only client refuses to send: another host, or a write."""


class SessionExpired(RuntimeError):
    """The school sent the request to its login page: the saved session has expired."""

    def __init__(self) -> None:
        super().__init__("the school's session has expired; sign in again")


def is_school_host(host: str | None) -> bool:
    host = (host or "").lower()
    return host == "skoleintra.dk" or host.endswith(ALLOWED_HOST_SUFFIX)


def is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and is_school_host(parsed.hostname)


def is_login_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return "/account/" in path or "login" in path or "signin" in path


def is_authenticated_page(url: str) -> bool:
    return is_allowed_url(url) and not is_login_url(url)


def is_read_post(path: str, read_posts: frozenset[str] = READ_POSTS) -> bool:
    path = path.rstrip("/").lower()
    return any(path.endswith("/" + endpoint) for endpoint in read_posts)


SENSITIVE_QUERY_PARTS = (
    "access_token",
    "auth",
    "code",
    "credential",
    "key",
    "password",
    "saml",
    "secret",
    "session",
    "signature",
    "state",
    "ticket",
    "token",
)


def require_allowed_url(url: str) -> str:
    if not is_allowed_url(url):
        raise ValueError("Only HTTPS skoleintra.dk URLs are allowed")
    return url


def sanitize_url(url: str) -> str:
    parsed = urlparse(url)
    safe_query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        sensitive = any(part in key_lower for part in SENSITIVE_QUERY_PARTS)
        safe_query.append((key, "[REDACTED]" if sensitive else value))
    return urlunparse(parsed._replace(query=urlencode(safe_query)))


class ReadOnlyClient(httpx.Client):
    """An httpx client that can only read from one school host."""

    def __init__(self, host: str, *, cookies: Any = None, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30, read_posts: frozenset[str] = READ_POSTS) -> None:
        if not is_school_host(host):
            raise ReadOnlyViolation("the read-only client only reaches SkoleIntra hosts")
        self.host = host.lower()
        self.read_posts = frozenset(read_posts)
        super().__init__(cookies=cookies, transport=transport, timeout=timeout, follow_redirects=True,
                         headers=HEADERS, event_hooks={"request": [self._each_hop]})

    def _refusal(self, request: httpx.Request) -> str | None:
        if request.url.scheme != "https" or request.url.host != self.host:
            return f"{request.method} to another host"
        if request.method in _READ_METHODS:
            return None
        if request.method == "POST" and is_read_post(request.url.path, self.read_posts):
            return None
        return f"{request.method} {request.url.path} is not a known read endpoint"

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        refusal = self._refusal(request)
        if refusal:
            raise ReadOnlyViolation(refusal)
        response = super().send(request, **kwargs)
        if is_login_url(str(response.url)):
            raise SessionExpired()
        return response

    def _each_hop(self, request: httpx.Request) -> None:
        # Runs before the first request and before each redirect. The first was
        # checked in send(), so a hop off the host or to a login page is the school
        # asking for a new login. A redirect never turns a read into a write.
        if request.url.host != self.host or is_login_url(str(request.url)):
            raise SessionExpired()
        refusal = self._refusal(request)
        if refusal:
            raise ReadOnlyViolation(refusal)
