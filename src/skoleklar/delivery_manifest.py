from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from skoleklar import health as stage_health
from skoleklar.brief import build_brief, render_brief_text
from skoleklar.contracts import validate_contract
from skoleklar.delivery_store import DeliveryObjectExistsError


CALENDAR_TYPES = {"event", "schedule_change"}
REMINDER_TYPES = {"bring", "homework", "parent_action", "reminder"}
MINIMUM_AUTO_PUBLISH_CONFIDENCE = 0.75


def build_delivery_manifest(
    items: list[dict[str, Any]],
    *,
    target_date: date,
    generated_at: datetime | None = None,
    expires_after: timedelta = timedelta(hours=36),
    coverage: dict[str, Any] | None = None,
    health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The phone's manifest; `health` (skoleklar.health) names in the brief what the scan missed."""
    generated = _as_utc(generated_at or datetime.now(UTC))
    latest_run_id = max(
        (int(item.get("harvest_run_id", 0)) for item in items),
        default=0,
    )
    active = [
        item
        for item in items
        if not item.get("completed_at") and not item.get("dismissed_at")
    ]
    brief = build_brief(
        active,
        target_date=target_date,
        latest_run_id=latest_run_id,
    )
    calendar: list[dict[str, Any]] = []
    reminders: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []

    for item in sorted(active, key=_item_sort_key):
        item_type = item["item_type"]
        if item_type not in CALENDAR_TYPES | REMINDER_TYPES:
            continue
        if _ended_before(item, target_date):
            # The phone never deletes, so a past item would only arrive as an
            # overdue reminder or an old calendar entry.
            continue
        reason = _review_reason(item)
        if reason:
            review.append(
                {
                    "stable_key": item["stable_key"],
                    "title": _phone_title(item),
                    "summary": _limited(str(item["summary"]), 1000),
                    "reason": _limited(reason, 500),
                }
            )
            continue
        if item_type in CALENDAR_TYPES:
            value = {
                "stable_key": item["stable_key"],
                "state": item.get("state", "active"),
                "title": _phone_title(item),
                "start": item["effective_date"],
                "end": item.get("end_date") or item["effective_date"],
                "all_day": True,
                "notes": _limited(str(item["summary"]), 2000),
                "source_url": _source_url(item),
            }
            value["revision"] = _revision(value)
            calendar.append(value)
        else:
            value = {
                "stable_key": item["stable_key"],
                "state": item.get("state", "active"),
                "list": (
                    "School · Bring home"
                    if item_type == "bring"
                    else "School · Deadlines"
                ),
                "title": _phone_title(item),
                "due": item["effective_date"],
                "notes": _limited(str(item["summary"]), 2000),
                "source_url": _source_url(item),
            }
            value["revision"] = _revision(value)
            reminders.append(value)

    material = {
        "target_date": target_date.isoformat(),
        "calendar": calendar,
        "reminders": reminders,
        "review": review,
        "brief": brief,
    }
    suffix = hashlib.sha256(_canonical_bytes(material)).hexdigest()[:12]
    generated_text = generated.isoformat().replace("+00:00", "Z")
    expires_text = (generated + expires_after).isoformat().replace("+00:00", "Z")
    brief_body = render_brief_text(brief)
    if coverage:
        if coverage.get("mode") == "off":
            brief_body += "\n\n🔒 Messages were not checked by privacy choice."
        elif coverage.get("mode") == "events-only":
            brief_body += (
                "\n\n🔒 Messages indexed · "
                f"{int(coverage.get('unread_count', 0))} unread · "
                f"{int(coverage.get('new_count', 0))} new · bodies not retained."
            )
    missing = stage_health.line(health)
    if missing:
        brief_body += f"\n\n⚠️ {missing}"
    manifest = {
        "schema_version": 1,
        "manifest_id": f"{generated.strftime('%Y%m%dT%H%M%SZ')}-{suffix}",
        "generated_at": generated_text,
        "expires_at": expires_text,
        "headline": _limited(brief["headline"], 160),
        "calendar": calendar,
        "reminders": reminders,
        "review": review,
        "brief": {
            "title": f"School brief · {target_date.isoformat()}",
            "body": _limited(brief_body, 6000),
            "signal": brief["signal"]["level"],
        },
    }
    if coverage is not None:
        manifest["coverage"] = coverage
    validate_contract("manifest-v1", manifest)
    return manifest


def publish_manifest(store: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    validate_contract("manifest-v1", manifest)
    object_name = f"manifests/{manifest['manifest_id']}.json"
    try:
        store.write_json(object_name, manifest, create_only=True)
    except DeliveryObjectExistsError:
        if store.read_json(object_name) != manifest:
            raise
    pointer = {
        "schema_version": 1,
        "manifest_id": manifest["manifest_id"],
        "object": object_name,
        "updated_at": manifest["generated_at"],
    }
    store.write_json("current.json", pointer)
    return pointer


def delivery_items_from_snapshot(
    store: Any, *, message_mode: str = "off"
) -> list[dict[str, Any]]:
    current = store.current_derived_items()
    message_events = (
        store.message_event_items() if message_mode == "events-only" else []
    )
    report = store.consolidation_report()
    if report is None or report["harvest_run_id"] != store.latest_completed_run_id():
        return current + message_events
    current_by_id = {int(item["id"]): item for item in current}
    active_current_ids = {
        item_id
        for item_id, item in current_by_id.items()
        if not item.get("completed_at") and not item.get("dismissed_at")
    }
    consolidated_member_ids = {
        int(member_id)
        for item in report["items"]
        for member_id in item["member_item_ids"]
    }
    if consolidated_member_ids != active_current_ids:
        return current + message_events
    enriched: list[dict[str, Any]] = []
    for consolidated in report["items"]:
        members = [
            current_by_id[member_id]
            for member_id in consolidated["member_item_ids"]
            if member_id in current_by_id
        ]
        if not members:
            continue
        item = dict(consolidated)
        for field in (
            "first_delivered_at",
            "last_delivered_at",
            "acknowledged_at",
            "completed_at",
            "dismissed_at",
        ):
            values = [member.get(field) for member in members if member.get(field)]
            item[field] = max(values) if values else None
        enriched.append(item)
    return enriched + message_events


def _review_reason(item: dict[str, Any]) -> str | None:
    if item.get("needs_review"):
        return item.get("review_reason") or "The source interpretation requires review."
    if float(item.get("confidence", 0)) < MINIMUM_AUTO_PUBLISH_CONFIDENCE:
        return "Confidence is below the automatic publishing threshold."
    if not _valid_date(item.get("effective_date")):
        return "A validated effective date is required for native publishing."
    if item["item_type"] in CALENDAR_TYPES and not bool(item.get("all_day")):
        return "A timed event cannot be published until explicit start and end times are available."
    if item.get("state", "active") not in {"active", "cancelled"}:
        return "The native item state is unsupported."
    return None


def _ended_before(item: dict[str, Any], target_date: date) -> bool:
    """True when the item's last day is before the manifest's day.

    Undated items stay: they go to review, where their missing date is the point.
    """
    last = item.get("end_date") if item["item_type"] in CALENDAR_TYPES else None
    for value in (last, item.get("effective_date")):
        if _valid_date(value):
            return date.fromisoformat(value) < target_date
    return False


def _phone_title(item: dict[str, Any]) -> str:
    title = str(item["title"]).strip()
    child = str(item.get("child") or "").strip()
    if child and child != "household" and not title.casefold().startswith(child.casefold()):
        title = f"{child} · {title}"
    return _limited(title, 200)


def _source_url(item: dict[str, Any]) -> str | None:
    urls = item.get("source_urls") or []
    return str(urls[0]) if urls else None


def _revision(value: dict[str, Any]) -> int:
    digest = hashlib.sha256(_canonical_bytes(value)).digest()
    return int.from_bytes(digest[:4], "big") % 2_147_483_647 + 1


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _item_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("effective_date") or "9999-12-31"),
        str(item.get("child") or ""),
        str(item.get("stable_key") or ""),
    )


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _limited(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)
