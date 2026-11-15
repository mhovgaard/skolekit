"""What every source shares: bounded scope, source records and authenticated GET.

Source modules expose a `SOURCE` reading one child within a bounded `Scope`.
Shared records and coverage make partial reads explicit. Harvest policies
belong to the product, not to the toolkit.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import Tag

from skolekit.profile import Child
from skolekit.records import SourceCheck
from skolekit.transport import is_login_url


@dataclass(frozen=True, slots=True)
class Scope:
    """How much of a source to read. The defaults are the current view."""

    # Front page and inbox: listing pages to read; None reads to the end.
    pages: int | None = 1
    # Calendar: whole weeks before and after the current one.
    weeks_back: int = 0
    weeks_ahead: int = 0
    # Calendar: the whole school year instead of the weeks around this one.
    school_year: bool = False
    # Weekly plans: the newest listed weeks to read; None reads every one.
    plan_weeks: int | None = 1
    # A bound on pages followed, whatever `pages` says.
    page_safety_limit: int = 100

    def __post_init__(self) -> None:
        for name in ("weeks_back", "weeks_ahead"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name.replace('_', ' ')} cannot be negative")
        for name in ("pages", "plan_weeks"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name.replace('_', ' ')} must be at least 1")


class CollectionError(RuntimeError):
    pass


def authenticated_get(client: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
    response = client.get(url, **kwargs)
    if not response.is_success or is_login_url(str(response.url)):
        raise CollectionError(f"Authenticated GET failed for {urlparse(url).path}")
    return response


def element_html(element: Tag | None) -> str:
    return str(element) if element is not None else ""


def absolute_links(element: Tag | None, base_url: str) -> list[str]:
    if element is None:
        return []
    links: list[str] = []
    for anchor in element.select("a[href]"):
        href = anchor.get("href") or ""
        if not href or href.lower().startswith("javascript:"):
            continue
        resolved = urljoin(base_url, href)
        if resolved not in links:
            links.append(resolved)
    return links



Collect = Callable[[httpx.Client, Child, Scope], SourceCheck]


@dataclass(frozen=True, slots=True)
class Source:
    """One kind of SkoleIntra page, read the same way at every school."""

    name: str
    collect: Collect
    # POST paths the source calls to read; the read-only client allows only these.
    read_posts: frozenset[str] = frozenset()
