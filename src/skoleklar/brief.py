from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any


DAY_SCOPED_TYPES = {
    "homework",
    "bring",
    "schedule_change",
    "substitute",
    "routine",
    "lesson_preview",
}
CHANGE_SCOPED_TYPES = {"event", "parent_action", "reminder", "context"}
ATTENTION_URGENCY = {"attention", "urgent", "critical"}
URGENT_URGENCY = {"urgent", "critical"}
COMPACT_HIDDEN_TYPES = {"lesson_preview", "routine"}


def build_brief(
    items: list[dict[str, Any]], *, target_date: date, latest_run_id: int
) -> dict[str, Any]:
    active = [
        item
        for item in items
        if not item.get("completed_at") and not item.get("dismissed_at")
    ]
    selected = [
        item
        for item in active
        if _include_item(item, target_date=target_date, latest_run_id=latest_run_id)
    ]
    signal = _traffic_light(selected, target_date=target_date)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    family_actions: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    public_items: list[dict[str, Any]] = []
    for item in selected:
        value = _public_item(item)
        public_items.append(value)
        if item["needs_review"]:
            review.append(value)
            continue
        if item["item_type"] in {"parent_action", "reminder"} and item[
            "audience"
        ] in {"parent", "family"}:
            family_actions.append(value)
        elif item["child"] == "household" or item["item_type"] == "context":
            notices.append(value)
        else:
            grouped[item["child"]].append(value)

    return {
        "date": target_date.isoformat(),
        "signal": signal,
        "headline": _headline(signal, selected),
        "children": [
            {"child": child, "items": child_items}
            for child, child_items in sorted(grouped.items())
        ],
        "family_actions": family_actions,
        "notices": notices,
        "review": review,
        "items": public_items,
    }


def build_brief_range(
    items: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
    compact: bool = False,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("Brief end date cannot be before its start date")
    active = [
        item
        for item in items
        if not item.get("completed_at") and not item.get("dismissed_at")
    ]
    selected = [
        item
        for item in active
        if _include_item_in_range(item, start_date=start_date, end_date=end_date)
        and (not compact or _include_compact_item(item))
    ]
    selected.sort(
        key=lambda item: (
            item.get("effective_date") or "9999-12-31",
            item.get("child") or "",
            item.get("item_type") or "",
            item.get("title") or "",
        )
    )
    atomic_items = [_public_item(item) for item in selected]
    public_items = (
        _compact_schedule_changes(atomic_items) if compact else atomic_items
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in public_items:
        effective = _parse_date(value.get("effective_date"))
        assert effective is not None
        display_date = max(effective, start_date)
        grouped[display_date.isoformat()].append(value)
    return {
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "compact": compact,
        "item_count": len(public_items),
        "atomic_item_count": len(atomic_items),
        "compaction": {
            "applied": compact,
            "display_item_count": len(public_items),
            "atomic_item_count": len(atomic_items),
            "collapsed_item_count": len(atomic_items) - len(public_items),
            "schedule_group_count": sum(
                bool(item.get("grouped")) for item in public_items
            ),
        },
        "days": [
            {"date": day.isoformat(), "items": grouped.get(day.isoformat(), [])}
            for day in _date_range(start_date, end_date)
        ],
        "items": public_items,
    }


def brief_item_stable_keys(items: list[dict[str, Any]]) -> list[str]:
    """Expand presentation groups back to durable workflow identities."""
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        member_keys = item.get("member_stable_keys")
        keys = member_keys if isinstance(member_keys, list) and member_keys else [
            item["stable_key"]
        ]
        for raw_key in keys:
            key = str(raw_key)
            if key not in seen:
                seen.add(key)
                result.append(key)
    return result


def _compact_schedule_changes(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group repeated timetable rows without a configured subject taxonomy."""
    members_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for item in items:
        key = _schedule_group_key(item)
        if key is not None:
            members_by_key[key].append(item)

    grouped_by_key = {
        key: _schedule_group(members)
        for key, members in members_by_key.items()
        if len(members) > 1
    }
    emitted: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = _schedule_group_key(item)
        if key not in grouped_by_key:
            result.append(item)
        elif key not in emitted:
            emitted.add(key)
            result.append(grouped_by_key[key])
    return result


def _schedule_group_key(item: dict[str, Any]) -> tuple[str, str, str] | None:
    if item.get("type") != "schedule_change":
        return None
    # Restricted message facts intentionally have generic titles. Keep them
    # separate because two private threads on one day need not describe one event.
    if str(item.get("stable_key") or "").startswith("message-event:"):
        return None
    child = str(item.get("child") or "").strip()
    effective_date = str(item.get("effective_date") or "").strip()
    title = _normalized_label(item.get("title"))
    if not child or not effective_date or not title:
        return None
    return child, effective_date, title


def _schedule_group(members: list[dict[str, Any]]) -> dict[str, Any]:
    members = sorted(members, key=lambda item: str(item.get("stable_key") or ""))
    first = members[0]
    details_by_summary: dict[str, dict[str, Any]] = {}
    member_stable_keys: list[str] = []
    member_ids: list[Any] = []
    source_urls: set[str] = set()
    for member in members:
        stable_key = str(member["stable_key"])
        member_stable_keys.append(stable_key)
        member_ids.append(member["id"])
        urls = {str(url) for url in member.get("source_urls") or []}
        source_urls.update(urls)
        detail_key = _normalized_label(member.get("summary")) or stable_key
        detail = details_by_summary.get(detail_key)
        if detail is None:
            details_by_summary[detail_key] = {
                "summary": str(member.get("summary") or ""),
                "member_count": 1,
                "member_ids": [member["id"]],
                "member_stable_keys": [stable_key],
                "source_urls": sorted(urls),
            }
        else:
            detail["member_count"] += 1
            detail["member_ids"].append(member["id"])
            detail["member_stable_keys"].append(stable_key)
            detail["source_urls"] = sorted(set(detail["source_urls"]) | urls)

    details = list(details_by_summary.values())
    title = str(first.get("title") or "Skemaændring")
    effective_date = str(first["effective_date"])
    times = sorted(
        {
            time_range
            for detail in details
            for time_range in _summary_time_ranges(detail["summary"])
        }
    )
    change_count = len(times) if times else len(details)
    summary = (
        f"{title}: {change_count} "
        f"{'ændring' if change_count == 1 else 'ændringer'} den {effective_date}"
    )
    if times:
        summary += " · kl. " + ", ".join(times)
    duplicate_count = len(members) - len(details)
    if duplicate_count:
        summary += (
            f" · {duplicate_count} overlappende "
            f"{'kildelinje' if duplicate_count == 1 else 'kildelinjer'} samlet"
        )
    summary += "."

    material = "\0".join(sorted(member_stable_keys)).encode("utf-8")
    end_dates = [str(item["end_date"]) for item in members if item.get("end_date")]
    return {
        "id": first["id"],
        "stable_key": "brief-group:" + hashlib.sha256(material).hexdigest(),
        "type": "schedule_change",
        "stream": first["stream"],
        "child": first["child"],
        "title": title,
        "summary": summary,
        "effective_date": effective_date,
        "end_date": max(end_dates) if end_dates else None,
        "actionability": _strongest_value(
            members,
            field="actionability",
            order=("informational", "recommended", "required"),
        ),
        "urgency": _strongest_value(
            members,
            field="urgency",
            order=("routine", "attention", "urgent", "critical"),
        ),
        "confidence": min(float(item.get("confidence", 0)) for item in members),
        "needs_review": any(bool(item.get("needs_review")) for item in members),
        "seen": all(bool(item.get("seen")) for item in members),
        "completed": all(bool(item.get("completed")) for item in members),
        "source_urls": sorted(source_urls),
        "grouped": True,
        "group_kind": "adaptive_schedule",
        "member_count": len(members),
        "detail_count": len(details),
        "duplicate_count": duplicate_count,
        "member_ids": member_ids,
        "member_stable_keys": member_stable_keys,
        "details": details,
    }


def _normalized_label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _summary_time_ranges(value: str) -> list[str]:
    return [
        f"{start}–{end}"
        for start, end in re.findall(
            r"\bkl\.\s*(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})",
            value,
        )
    ]


def _strongest_value(
    items: list[dict[str, Any]], *, field: str, order: tuple[str, ...]
) -> str:
    rank = {value: index for index, value in enumerate(order)}
    return max(
        (str(item.get(field) or order[0]) for item in items),
        key=lambda value: rank.get(value, 0),
    )


def _include_item(
    item: dict[str, Any], *, target_date: date, latest_run_id: int
) -> bool:
    effective = _parse_date(item.get("effective_date"))
    item_end = _parse_date(item.get("end_date"))
    if item_end is not None and item_end < target_date:
        return False
    if effective is not None and effective > target_date:
        return False
    if item["item_type"] == "event":
        event_end = item_end or effective
        if event_end is not None and event_end < target_date:
            return False
    if effective == target_date:
        return True
    if (
        effective is not None
        and effective <= target_date
        and item["item_type"] in {"parent_action", "reminder"}
        and item["actionability"] in {"required", "recommended"}
    ):
        return True
    if item["urgency"] in URGENT_URGENCY and item["harvest_run_id"] == latest_run_id:
        return True
    return (
        item["harvest_run_id"] == latest_run_id
        and item["item_type"] in CHANGE_SCOPED_TYPES
    )


def _include_item_in_range(
    item: dict[str, Any], *, start_date: date, end_date: date
) -> bool:
    effective = _parse_date(item.get("effective_date"))
    if effective is None or effective > end_date:
        return False
    item_end = _parse_date(item.get("end_date")) or effective
    return item_end >= start_date


def _include_compact_item(item: dict[str, Any]) -> bool:
    return not (
        item["item_type"] in COMPACT_HIDDEN_TYPES
        and item["actionability"] == "informational"
        and item["urgency"] == "routine"
        and not item["needs_review"]
    )


def _traffic_light(items: list[dict[str, Any]], *, target_date: date) -> dict[str, Any]:
    red: list[dict[str, Any]] = []
    amber: list[dict[str, Any]] = []
    for item in items:
        urgent = item["urgency"] in URGENT_URGENCY or _due_action_is_urgent(
            item, target_date=target_date
        )
        seen = bool(item.get("first_delivered_at") or item.get("acknowledged_at"))
        if urgent and not seen:
            red.append(item)
        elif (
            item["needs_review"]
            or urgent
            or item["urgency"] in ATTENTION_URGENCY
            or item["actionability"] in {"required", "recommended"}
        ):
            amber.append(item)
    if red:
        return {
            "level": "red",
            "reason": f"{len(red)} urgent unseen item{'s' if len(red) != 1 else ''}",
            "item_ids": [item["id"] for item in red],
            "unseen_urgent_count": len(red),
            "attention_count": len(amber),
            "review_count": sum(bool(item["needs_review"]) for item in items),
        }
    if amber:
        review_count = sum(bool(item["needs_review"]) for item in amber)
        attention_count = len(amber) - review_count
        reason_parts = []
        if attention_count:
            reason_parts.append(f"{attention_count} attention")
        if review_count:
            reason_parts.append(f"{review_count} review")
        return {
            "level": "amber",
            "reason": " · ".join(reason_parts),
            "item_ids": [item["id"] for item in amber],
            "unseen_urgent_count": 0,
            "attention_count": attention_count,
            "review_count": review_count,
        }
    return {
        "level": "green",
        "reason": "no urgent changes",
        "item_ids": [],
        "unseen_urgent_count": 0,
        "attention_count": 0,
        "review_count": 0,
    }


def _headline(signal: dict[str, Any], selected: list[dict[str, Any]]) -> str:
    return f"{signal['level'].upper()} · {len(selected)} relevant item{'s' if len(selected) != 1 else ''}"


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "stable_key": item["stable_key"],
        "type": item["item_type"],
        "stream": item["stream"],
        "child": item["child"],
        "title": item["title"],
        "summary": item["summary"],
        "effective_date": item["effective_date"],
        "end_date": item["end_date"],
        "actionability": item["actionability"],
        "urgency": item["urgency"],
        "confidence": item["confidence"],
        "needs_review": item["needs_review"],
        "seen": bool(item.get("first_delivered_at") or item.get("acknowledged_at")),
        "completed": bool(item.get("completed_at")),
        "source_urls": item.get("source_urls", []),
        **({"activity_kind": item["activity_kind"]} if item.get("activity_kind") else {}),
        **({"state": "cancelled"} if item.get("state") == "cancelled" else {}),
    }


def render_brief_text(brief: dict[str, Any]) -> str:
    symbols = {"green": "🟢", "amber": "🟠", "red": "🔴"}
    signal = brief["signal"]
    lines = [
        f"{symbols[signal['level']]} {signal['level'].upper()} — {signal['reason']}"
    ]
    for child in brief["children"]:
        lines.append("")
        lines.append(child["child"])
        lines.extend(f"• {item['summary']}" for item in child["items"])
    if brief["family_actions"]:
        lines.append("")
        lines.append("Family actions")
        lines.extend(f"• {item['summary']}" for item in brief["family_actions"])
    if brief["notices"]:
        lines.append("")
        lines.append("School")
        lines.extend(f"• {item['summary']}" for item in brief["notices"])
    if brief["review"]:
        lines.append("")
        lines.append("Review")
        lines.extend(f"• {item['summary']}" for item in brief["review"])
    coverage_line = _message_coverage_line(brief.get("coverage"))
    if coverage_line:
        lines.extend(("", coverage_line))
    return "\n".join(lines)


def render_brief_range_text(brief: dict[str, Any]) -> str:
    lines = [
        f"School brief · {brief['from']} to {brief['to']} · "
        f"{brief['item_count']} item{'s' if brief['item_count'] != 1 else ''}"
    ]
    for day in brief["days"]:
        if not day["items"]:
            continue
        lines.extend(("", day["date"]))
        for item in day["items"]:
            child = "School" if item["child"] == "household" else item["child"]
            lines.append(f"• {child} · {item['summary']}")
    if brief["item_count"] == 0:
        lines.append("No dated items in this range.")
    coverage_line = _message_coverage_line(brief.get("coverage"))
    if coverage_line:
        lines.extend(("", coverage_line))
    return "\n".join(lines)


def _message_coverage_line(coverage: Any) -> str:
    if not isinstance(coverage, dict):
        return ""
    if coverage.get("mode") == "off":
        return "🔒 Messages were not checked by privacy choice."
    if coverage.get("mode") == "events-only":
        return (
            "🔒 Messages indexed · "
            f"{int(coverage.get('unread_count', 0))} unread · "
            f"{int(coverage.get('new_count', 0))} new · bodies not retained."
        )
    return ""


def _date_range(start_date: date, end_date: date) -> list[date]:
    return [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]


def _due_action_is_urgent(item: dict[str, Any], *, target_date: date) -> bool:
    effective = _parse_date(item.get("effective_date"))
    return (
        item["item_type"] in {"parent_action", "reminder"}
        and item["actionability"] == "required"
        and not item["needs_review"]
        and effective is not None
        and effective <= target_date
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
