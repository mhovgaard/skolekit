"""The front page: the notice board's items, following its 'show more' pages."""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from skolekit.parsers.weekly_plan import content_to_text
from skolekit.profile import Child
from skolekit.records import SourceCheck, SourceRecord
from skolekit.sources.base import (
    Source,
    Scope,
    absolute_links,
    authenticated_get,
    element_html,
)

SHOW_MORE_PATTERN = re.compile(
    r'"UrlForRetrievingMoreNewsItems"\s*:\s*"([^"]+)"'
)


def _news_records(
    fragment: str, *, child: Child, source_url: str
) -> list[SourceRecord]:
    soup = BeautifulSoup(fragment, "html.parser")
    records: list[SourceRecord] = []
    for item in soup.select(".sk-news-item"):
        feed_type = item.get("data-feed-item-type") or "unknown"
        feed_id = item.get("data-feed-item-id")
        if not feed_id:
            continue
        content = item.select_one(".sk-news-item-content")
        body_html = element_html(content)
        body_text = content_to_text(body_html)
        timestamps = [
            " ".join(node.get_text(" ", strip=True).split())
            for node in item.select(".sk-news-item-timestamp")
        ]
        attachments = [
            {
                "url": urljoin(source_url, anchor.get("href") or ""),
                "title": " ".join(anchor.get_text(" ", strip=True).split()),
            }
            for anchor in item.select(".sk-attachments-list a[href]")
        ]
        author = " ".join(
            (item.select_one(".sk-news-item-author") or Tag(name="span"))
            .get_text(" ", strip=True)
            .split()
        )
        audience = " ".join(
            (item.select_one(".sk-news-item-for") or Tag(name="span"))
            .get_text(" ", strip=True)
            .split()
        )
        footer = item.select_one(".sk-news-item-footer")
        footer_text = " ".join(footer.get_text(" ", strip=True).split()) if footer else ""
        title = body_text.splitlines()[0][:200] if body_text else f"Notice {feed_id}"
        links = absolute_links(item, source_url)
        record_url = f"{source_url}#{feed_type}:{feed_id}"
        raw_html = element_html(item)
        records.append(
            SourceRecord(
                student_id=child.student_id,
                child=child.child,
                source_type="frontpage",
                source_key=f"{feed_type}:{feed_id}",
                source_url=record_url,
                title=title,
                body_html=body_html,
                body_text=body_text,
                dates=[
                    {"kind": "display_timestamp", "value": value}
                    for value in timestamps
                ],
                attachments=attachments,
                links=links,
                source_data={
                    "feed_type": feed_type,
                    "feed_id": feed_id,
                    "author": author,
                    "audience": audience,
                    "footer_text": footer_text,
                    "timestamps": timestamps,
                },
                content_data={
                    "author": author,
                    "audience": audience,
                    "footer_text": footer_text,
                },
                raw_payload=raw_html,
            )
        )
    return records


def collect_frontpage(
    client: httpx.Client,
    child: Child,
    *,
    page_limit: int | None = None,
    max_pages: int = 100,
) -> SourceCheck:
    if page_limit is not None and page_limit < 1:
        raise ValueError("Front-page limit must be at least 1")
    url = child.route("Index")
    response = authenticated_get(client, url)
    records = _news_records(response.text, child=child, source_url=url)
    soup = BeautifulSoup(response.text, "html.parser")
    inline = "\n".join(
        script.get_text() for script in soup.find_all("script") if not script.get("src")
    )
    endpoint_match = SHOW_MORE_PATTERN.search(inline)
    complete = endpoint_match is None
    pages = 1
    seen_keys = {record.source_key for record in records}
    cursor = {
        record.source_data["feed_type"]: record.source_data["feed_id"]
        for record in records
    }
    seen_cursors: set[str] = set()

    additional_limit = (
        max_pages if page_limit is None else min(max_pages, page_limit - 1)
    )
    if endpoint_match and additional_limit:
        endpoint = urljoin(url, endpoint_match.group(1))
        for _ in range(additional_limit):
            cursor_key = json.dumps(cursor, sort_keys=True)
            if not cursor or cursor_key in seen_cursors:
                break
            seen_cursors.add(cursor_key)
            page_response = client.post(endpoint, data={"pagings": cursor_key})
            page_response.raise_for_status()
            payload = page_response.json()
            page_records = _news_records(
                payload.get("View") or "", child=child, source_url=url
            )
            added = 0
            for record in page_records:
                cursor[record.source_data["feed_type"]] = record.source_data["feed_id"]
                if record.source_key in seen_keys:
                    continue
                seen_keys.add(record.source_key)
                records.append(record)
                added += 1
            pages += 1
            if not payload.get("HasMoreItems"):
                complete = True
                break
            if not added:
                break
    scope_complete = complete or (page_limit is not None and pages >= page_limit)
    return SourceCheck(
        student_id=child.student_id,
        child=child.child,
        source_type="frontpage",
        records=records,
        complete=scope_complete,
        metadata={"pages": pages, "exhausted": complete},
        coverage={"kind": "full"}
        if complete
        else {"kind": "page_window", "pages": pages},
    )



def _collect(client: httpx.Client, child: Child, scope: Scope) -> SourceCheck:
    return collect_frontpage(client, child, page_limit=scope.pages, max_pages=scope.page_safety_limit)


SOURCE = Source("frontpage", _collect, read_posts=frozenset({"newsitems/showmore"}))
