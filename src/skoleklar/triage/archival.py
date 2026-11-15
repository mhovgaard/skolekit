from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from skoleklar.triage.derivation import DEFAULT_BASE_URL, DEFAULT_MODEL, OpenRouterClient
from skoleklar.triage.prompt_manifest import load_prompt_manifest


def archival_manifest():
    """The archive stage's manifest with the household's names in its slots."""
    return load_prompt_manifest("archive-v1")


ARCHIVAL_POLICY_VERSION = "archive-v1"
STALE_AFTER_DAYS = 30
TIME_BOUND_TYPES = {
    "bring",
    "event",
    "homework",
    "lesson_preview",
    "routine",
    "schedule_change",
    "substitute",
}


def plan_archival(
    items: list[dict[str, Any]],
    *,
    as_of: date,
    stale_after_days: int = STALE_AFTER_DAYS,
) -> dict[str, list[dict[str, Any]]]:
    """Partition live items into policy decisions and agent-review candidates."""
    decisions: list[dict[str, Any]] = []
    agent_candidates: list[dict[str, Any]] = []
    cutoff = as_of - timedelta(days=stale_after_days)

    for item in _unique_items(items):
        base = {
            "item_id": int(item["id"]),
            "stable_key": str(item["stable_key"]),
        }
        if item.get("completed_at"):
            decisions.append(
                {
                    **base,
                    "decision": "archive",
                    "reason": "workflow_completed",
                    "method": "policy",
                }
            )
            continue
        if item.get("dismissed_at"):
            decisions.append(
                {
                    **base,
                    "decision": "archive",
                    "reason": "workflow_dismissed",
                    "method": "policy",
                }
            )
            continue

        boundary = _date_value(item.get("end_date")) or _date_value(
            item.get("effective_date")
        )
        expired = boundary is not None and boundary < as_of
        review_as_of = _date_value(item.get("archive_review_as_of"))
        review_is_current = review_as_of is not None and review_as_of >= cutoff
        if expired and item.get("first_delivered_at"):
            decisions.append(
                {
                    **base,
                    "decision": "retain",
                    "reason": "delivered_item_requires_tombstone_support",
                    "method": "policy",
                }
            )
        elif expired and item.get("item_type") in TIME_BOUND_TYPES:
            decisions.append(
                {
                    **base,
                    "decision": "archive",
                    "reason": f"expired_{item['item_type']}",
                    "method": "policy",
                }
            )
        elif expired and item.get("item_type") == "context" and item.get(
            "actionability"
        ) == "informational":
            decisions.append(
                {
                    **base,
                    "decision": "archive",
                    "reason": "expired_informational_context",
                    "method": "policy",
                }
            )
        elif expired and review_is_current:
            decisions.append(
                {
                    **base,
                    "decision": "retain",
                    "reason": "agent_retention_current",
                    "method": "policy",
                }
            )
        elif expired:
            agent_candidates.append(
                _agent_candidate(item, reason="expired_action_or_reminder")
            )
        elif boundary is not None:
            decisions.append(
                {
                    **base,
                    "decision": "retain",
                    "reason": "current_or_future",
                    "method": "policy",
                }
            )
        elif review_is_current:
            decisions.append(
                {
                    **base,
                    "decision": "retain",
                    "reason": "agent_retention_current",
                    "method": "policy",
                }
            )
        elif _date_value(item.get("workflow_first_seen_at")) is not None and _date_value(
            item.get("workflow_first_seen_at")
        ) <= cutoff:
            agent_candidates.append(_agent_candidate(item, reason="stale_without_end_date"))
        else:
            decisions.append(
                {
                    **base,
                    "decision": "retain",
                    "reason": "undated_and_recent",
                    "method": "policy",
                }
            )

    return {"decisions": decisions, "agent_candidates": agent_candidates}


def archival_document(
    candidates: list[dict[str, Any]], *, as_of: date
) -> dict[str, Any]:
    return {
        "as_of": as_of.isoformat(),
        "policy_version": ARCHIVAL_POLICY_VERSION,
        "candidates": candidates,
    }


def materialize_agent_decisions(
    output: dict[str, Any], *, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    candidate_by_key = {item["stable_key"]: item for item in candidates}
    returned = output.get("decisions")
    if not isinstance(returned, list):
        raise ValueError("Archival review must return a decisions array")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for decision in returned:
        stable_key = str(decision.get("stable_key") or "")
        if stable_key not in candidate_by_key:
            raise ValueError(f"Archival review returned unknown stable key: {stable_key}")
        if stable_key in seen:
            raise ValueError(f"Archival review repeated stable key: {stable_key}")
        seen.add(stable_key)
        value = decision.get("decision")
        if value not in {"archive", "retain"}:
            raise ValueError(f"Invalid archival decision for {stable_key}: {value}")
        reason = str(decision.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"Archival review omitted a reason for {stable_key}")
        candidate = candidate_by_key[stable_key]
        result.append(
            {
                "item_id": candidate["item_id"],
                "stable_key": stable_key,
                "decision": value,
                "reason": reason[:240],
                "method": "agent",
            }
        )
    missing = set(candidate_by_key) - seen
    if missing:
        raise ValueError(f"Archival review omitted stable keys: {sorted(missing)}")
    return result


class OpenRouterArchivalClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
    ) -> None:
        self.client = OpenRouterClient(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
        )

    def review(self, document: dict[str, Any]) -> dict[str, Any]:
        response = self.client.generate(
            document,
            manifest=archival_manifest(),
            max_output_tokens=4000,
        )
        return {
            "response_id": response["response_id"],
            "usage": response["usage"],
            "output": response["output"],
        }


def _unique_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for item in sorted(items, key=lambda value: int(value["id"])):
        by_key[str(item["stable_key"])] = item
    return list(by_key.values())


def _date_value(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _agent_candidate(item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "item_id": int(item["id"]),
        "stable_key": str(item["stable_key"]),
        "candidate_reason": reason,
        "child": item["child"],
        "type": item["item_type"],
        "actionability": item["actionability"],
        "title": item["title"],
        "summary": item["summary"],
        "effective_date": item.get("effective_date"),
        "end_date": item.get("end_date"),
        "first_seen_at": item.get("workflow_first_seen_at"),
        "needs_review": bool(item.get("needs_review")),
        "review_reason": item.get("review_reason") or "",
    }
