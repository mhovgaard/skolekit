from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calendar_source_key(kind: str, raw: dict[str, Any]) -> str:
    event_id = raw.get("originalId") or raw.get("id")
    if kind != "LessonsEvents":
        return f"{kind}:{event_id}"
    identity = {
        "start": raw.get("start") or raw.get("startDate"),
        "end": raw.get("end") or raw.get("endDate"),
        "class": raw.get("lessonClassName") or raw.get("className"),
        "lesson": raw.get("lessonName"),
        "staff": raw.get("staffName"),
    }
    if any(value is None for value in identity.values()):
        return f"{kind}:{event_id}"
    return f"{kind}:slot:{canonical_hash(identity)}"


@dataclass(slots=True)
class SourceRecord:
    student_id: int
    child: str
    source_type: str
    source_key: str
    source_url: str
    title: str = ""
    body_html: str = ""
    body_text: str = ""
    dates: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[Any] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    source_data: dict[str, Any] = field(default_factory=dict)
    content_data: dict[str, Any] = field(default_factory=dict)
    state_data: dict[str, Any] = field(default_factory=dict)
    raw_payload: Any = None

    def identity(self) -> tuple[int, str, str]:
        return self.student_id, self.source_type, self.source_key

    def hashes(self) -> dict[str, str]:
        raw_value = self.raw_payload
        if raw_value is None:
            raw_value = {
                "title": self.title,
                "body_html": self.body_html,
                "dates": self.dates,
                "attachments": self.attachments,
                "links": self.links,
                "source_data": self.source_data,
                "state_data": self.state_data,
            }
        return {
            "raw_hash": canonical_hash(raw_value),
            "content_hash": canonical_hash(
                {
                    "title": self.title,
                    "body_text": self.body_text,
                    "dates": self.dates,
                    "attachments": self.attachments,
                    "links": self.links,
                    "content_data": self.content_data,
                }
            ),
            "state_hash": canonical_hash(self.state_data),
        }

    def as_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": 1,
            "student_id": self.student_id,
            "child": self.child,
            "source_type": self.source_type,
            "source_key": self.source_key,
            "source_url": self.source_url,
            "title": self.title,
            "body_html": self.body_html,
            "body_text": self.body_text,
            "dates": self.dates,
            "attachments": self.attachments,
            "links": self.links,
            "source_data": self.source_data,
            "content_data": self.content_data,
            "state_data": self.state_data,
            "raw_payload": self.raw_payload,
        }
        value.update(self.hashes())
        return value


@dataclass(slots=True)
class SourceCheck:
    student_id: int
    child: str
    source_type: str
    records: list[SourceRecord] = field(default_factory=list)
    complete: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(
        default_factory=lambda: {"kind": "full"}
    )

    @property
    def status(self) -> str:
        return "ok" if self.error is None else "error"
