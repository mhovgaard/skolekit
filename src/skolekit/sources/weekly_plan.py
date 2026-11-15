"""Weekly plans: the class's plan for each listed week."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from skolekit.parsers.weekly_plan import parse_weekly_plan_html
from skolekit.profile import Child
from skolekit.records import SourceCheck, SourceRecord
from skolekit.sources.base import Scope, Source, authenticated_get

WEEK_ITEM_PATTERN = re.compile(r"/item/class/(?P<week>\d{1,2})-(?P<year>\d{4})$")


def listed_weeks(client: httpx.Client, child: Child) -> dict[str, tuple[int, int]]:
    """Each listed plan's page, with its (year, week), in the list's order."""
    list_url = child.weekly_route("list")
    response = authenticated_get(client, list_url)
    soup = BeautifulSoup(response.text, "html.parser")
    url_entries: dict[str, tuple[int, int]] = {}
    for anchor in soup.select("a[href]"):
        url = urljoin(list_url, anchor.get("href") or "")
        match = WEEK_ITEM_PATTERN.search(urlparse(url).path)
        if match:
            url_entries[url] = (int(match.group("year")), int(match.group("week")))
    return url_entries


def read_plan(client: httpx.Client, child: Child, url: str) -> dict[str, Any]:
    """One week's plan, parsed with the school's wording."""
    response = authenticated_get(client, url)
    return parse_weekly_plan_html(response.text, str(response.url), wording=child.school.wording)


def collect_weekly_plans(
    client: httpx.Client,
    child: Child,
    *,
    scope: Scope | None = None,
) -> SourceCheck:
    scope = scope or Scope()
    url_entries = listed_weeks(client, child)
    urls = [
        item[0]
        for item in sorted(url_entries.items(), key=lambda item: item[1])
    ]
    if scope.plan_weeks is not None:
        urls = urls[-scope.plan_weeks :]
    records: list[SourceRecord] = []
    for url in urls:
        plan = read_plan(client, child, url)
        match = WEEK_ITEM_PATTERN.search(urlparse(url).path)
        body_html_parts: list[str] = []
        body_text_parts: list[str] = []
        attachments: list[Any] = list(plan.get("attachments") or [])
        for item in plan["general_plans"]:
            body_html_parts.append(item["content_html"])
            body_text_parts.append(item["content_text"])
            attachments.extend(item.get("attachments") or [])
        for day in plan["days"]:
            for item in day["plans"]:
                body_html_parts.append(item["content_html"])
                body_text_parts.append(item["content_text"])
                attachments.extend(item.get("attachments") or [])
        records.append(
            SourceRecord(
                student_id=child.student_id,
                child=child.child,
                source_type="weekly_plan",
                source_key=f"class:{match.group('week')}-{match.group('year')}",
                source_url=url,
                title=plan["week"].get("label") or f"Week {match.group('week')}",
                body_html="\n".join(body_html_parts),
                body_text="\n".join(value for value in body_text_parts if value),
                dates=[
                    {"kind": "source_day", "value": day.get("date")}
                    for day in plan["days"]
                    if day.get("date")
                ],
                attachments=attachments,
                source_data=plan,
                content_data={
                    "week": plan["week"],
                    "general_plans": plan["general_plans"],
                    "days": plan["days"],
                },
                state_data={
                    "has_drafts": any(
                        item.get("is_draft")
                        for item in plan["general_plans"]
                        + [plan for day in plan["days"] for plan in day["plans"]]
                    )
                },
                raw_payload=plan,
            )
        )
    return SourceCheck(
        student_id=child.student_id,
        child=child.child,
        source_type="weekly_plan",
        records=records,
        metadata={"weeks": len(records), "available_weeks": len(url_entries)},
        coverage={"kind": "full"}
        if scope.plan_weeks is None
        else {
            "kind": "record_keys",
            "source_keys": [record.source_key for record in records],
        },
    )



def _collect(client: httpx.Client, child: Child, scope: Scope) -> SourceCheck:
    return collect_weekly_plans(client, child, scope=scope)


SOURCE = Source("weekly_plan", _collect)
