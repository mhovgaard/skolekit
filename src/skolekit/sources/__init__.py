"""The sources: one module per kind of published SkoleIntra page.

`SOURCES` lists them. Each reads one child's pages within a `Scope`, whose
defaults are the current view: the first front page, the current week. The
harvest over every source and child, with its policies, belongs to a product
built on the toolkit. Messages are not a source: their text is
never stored, and `skolekit.sources.messages` reads them live.
"""
from __future__ import annotations

from skolekit.sources import calendar, forms, frontpage, weekly_plan
from skolekit.sources.base import (
    CollectionError,
    Scope,
    Source,
    absolute_links,
    authenticated_get,
    element_html,
)
from skolekit.sources.calendar import collect_calendar, recent_calendar_bounds, school_year_bounds
from skolekit.sources.forms import collect_forms
from skolekit.sources.frontpage import collect_frontpage
from skolekit.sources.messages import fetch_all_conversations, fetch_thread_messages
from skolekit.sources.weekly_plan import collect_weekly_plans

SOURCES: tuple[Source, ...] = (
    frontpage.SOURCE,
    calendar.SOURCE,
    weekly_plan.SOURCE,
    forms.SOURCE,
)

__all__ = [
    "SOURCES", "CollectionError", "Scope", "Source", "absolute_links", "authenticated_get",
    "collect_calendar", "collect_forms", "collect_frontpage", "collect_weekly_plans", "element_html",
    "fetch_all_conversations", "fetch_thread_messages", "recent_calendar_bounds", "school_year_bounds",
]
