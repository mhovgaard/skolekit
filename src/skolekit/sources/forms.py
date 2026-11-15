"""Forms: the open and closed lists, and each form's questions and answers."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from skolekit.parsers.weekly_plan import content_to_text
from skolekit.profile import Child
from skolekit.records import SourceCheck, SourceRecord
from skolekit.sources.base import (
    CollectionError,
    Scope,
    Source,
    absolute_links,
    authenticated_get,
    element_html,
)

# A form keeps one key whether its link is open, closed or ends in `/edit`.
FORM_ID_PATTERN = re.compile(
    r"/forms/(?:open|closed)/(?P<kind>[^/]+)/(?P<id>[^/]+?)(?:/edit)?/?$"
)


def _form_questions(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    questions: list[dict[str, Any]] = []
    for control in soup.select("select, textarea, input[type=radio], input[type=checkbox]"):
        name = control.get("name") or ""
        control_id = control.get("id") or ""
        label = soup.find("label", attrs={"for": control_id}) if control_id else None
        options = []
        if control.name == "select":
            options = [
                {
                    "text": " ".join(option.get_text(" ", strip=True).split()),
                    "value": option.get("value") or "",
                    "selected": option.has_attr("selected"),
                }
                for option in control.find_all("option")
            ]
        questions.append(
            {
                "name": name,
                "type": control.name
                if control.name != "input"
                else control.get("type") or "text",
                "label": " ".join(label.get_text(" ", strip=True).split())
                if label
                else "",
                "options": options,
                "value": control.get("value") or ""
                if control.name != "textarea"
                else control.get_text(),
            }
        )
    return questions


def _form_record(
    client: httpx.Client,
    child: Child,
    raw: dict[str, Any],
    *,
    list_state: str,
) -> SourceRecord:
    detail_url = urljoin(child.route(f"forms/{list_state}"), raw.get("Url") or "")
    match = FORM_ID_PATTERN.search(urlparse(detail_url).path)
    source_key = (
        f"{match.group('kind')}:{match.group('id')}" if match else detail_url
    )
    detail = authenticated_get(client, detail_url)
    detail_soup = BeautifulSoup(detail.text, "html.parser")
    heading = detail_soup.select_one(".sk-l-content h3")
    content = heading.find_parent(class_="sk-white-box") if heading else None
    body_html = element_html(content)
    body_text = content_to_text(body_html)
    answer_anchor = detail_soup.select_one('a[href$="/answer"]')
    answer_url = urljoin(detail_url, answer_anchor.get("href")) if answer_anchor else ""
    questions: list[dict[str, Any]] = []
    if answer_url:
        answer = authenticated_get(client, answer_url)
        questions = _form_questions(answer.text)

    attachments = [
        {
            "url": link,
            "title": "",
        }
        for link in absolute_links(content, detail_url)
        if "/answer" not in link and "/forms/open" not in link
    ]
    status = raw.get("Status") or ""
    deadline = raw.get("Deadline") or ""
    return SourceRecord(
        student_id=child.student_id,
        child=child.child,
        source_type="forms",
        source_key=source_key,
        source_url=detail_url,
        title=raw.get("Name") or "",
        body_html=body_html,
        body_text=body_text,
        dates=[{"kind": "deadline", "value": deadline}] if deadline else [],
        attachments=attachments,
        links=[value for value in [detail_url, answer_url] if value],
        source_data={
            "creator": raw.get("CreatedBy") or "",
            "type": raw.get("Type") or "",
            "order_date": raw.get("OrderDate") or "",
            "results_link": raw.get("ResultsLink"),
            "open_in_new_tab": bool(raw.get("OpenInNewTab")),
            "is_app": bool(raw.get("IsApp")),
            "questions": questions,
        },
        content_data={
            "creator": raw.get("CreatedBy") or "",
            "type": raw.get("Type") or "",
            "questions": questions,
        },
        state_data={"list_state": list_state, "status": status},
        raw_payload={"list_item": raw, "detail_html": body_html, "questions": questions},
    )


def collect_forms(client: httpx.Client, child: Child,
                  states: tuple[str, ...] = ("open", "closed")) -> SourceCheck:
    records: list[SourceRecord] = []
    counts: dict[str, int] = {}
    for list_state in states:
        list_url = child.route(f"forms/{list_state}")
        response = authenticated_get(client, list_url)
        soup = BeautifulSoup(response.text, "html.parser")
        root = soup.select_one("[data-vue-component-data]")
        if root is None:
            raise CollectionError(f"Forms payload missing for {child.child}/{list_state}")
        payload = json.loads(root.get("data-vue-component-data") or "{}")
        items = [item for item in payload.get("Items") or [] if isinstance(item, dict)]
        counts[list_state] = len(items)
        records.extend(
            _form_record(client, child, item, list_state=list_state) for item in items
        )
    return SourceCheck(
        student_id=child.student_id,
        child=child.child,
        source_type="forms",
        records=records,
        metadata=counts,
    )



def _collect(client: httpx.Client, child: Child, scope: Scope) -> SourceCheck:
    return collect_forms(client, child)


SOURCE = Source("forms", _collect)
