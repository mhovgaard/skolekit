from datetime import date

import pytest

from skoleklar.triage.archival import materialize_agent_decisions, plan_archival


def item(item_id: int, **overrides: object) -> dict:
    value = {
        "id": item_id,
        "stable_key": f"stable-{item_id}",
        "item_type": "homework",
        "child": "Beta",
        "title": "Læs side 10",
        "summary": "Læs side 10 til mandag.",
        "effective_date": "2026-09-01",
        "end_date": "2026-09-01",
        "actionability": "required",
        "needs_review": False,
        "review_reason": "",
        "workflow_first_seen_at": "2026-08-20T08:00:00+00:00",
        "first_delivered_at": None,
        "completed_at": None,
        "dismissed_at": None,
        "archive_review_as_of": None,
    }
    value.update(overrides)
    return value


def test_policy_archives_expired_time_bound_items_even_when_review_flagged() -> None:
    plan = plan_archival(
        [item(1, needs_review=True, review_reason="Evidence needs review")],
        as_of=date(2026, 9, 12),
    )

    assert plan["agent_candidates"] == []
    assert plan["decisions"][0]["decision"] == "archive"
    assert plan["decisions"][0]["reason"] == "expired_homework"


def test_policy_retains_future_and_delivered_items() -> None:
    plan = plan_archival(
        [
            item(1, effective_date="2026-09-14", end_date="2026-09-14"),
            item(2, first_delivered_at="2026-09-02T08:00:00+00:00"),
        ],
        as_of=date(2026, 9, 12),
    )

    assert [decision["decision"] for decision in plan["decisions"]] == [
        "retain",
        "retain",
    ]
    assert plan["decisions"][1]["reason"] == "delivered_item_requires_tombstone_support"


def test_policy_routes_ambiguous_expired_and_stale_undated_items_to_agent() -> None:
    plan = plan_archival(
        [
            item(1, item_type="parent_action"),
            item(
                2,
                item_type="context",
                actionability="informational",
                effective_date=None,
                end_date=None,
                workflow_first_seen_at="2026-07-01T08:00:00+00:00",
            ),
        ],
        as_of=date(2026, 9, 12),
    )

    assert {candidate["candidate_reason"] for candidate in plan["agent_candidates"]} == {
        "expired_action_or_reminder",
        "stale_without_end_date",
    }


def test_recent_agent_retention_is_not_reviewed_on_every_harvest() -> None:
    plan = plan_archival(
        [item(1, item_type="parent_action", archive_review_as_of="2026-09-12")],
        as_of=date(2026, 9, 13),
    )

    assert plan["agent_candidates"] == []
    assert plan["decisions"][0]["reason"] == "agent_retention_current"


def test_agent_decisions_must_exactly_partition_candidates() -> None:
    candidate = {
        "item_id": 1,
        "stable_key": "stable-1",
    }
    decisions = materialize_agent_decisions(
        {
            "decisions": [
                {
                    "stable_key": "stable-1",
                    "decision": "archive",
                    "reason": "Arrangementet er passeret.",
                }
            ]
        },
        candidates=[candidate],
    )

    assert decisions[0]["method"] == "agent"
    with pytest.raises(ValueError, match="omitted"):
        materialize_agent_decisions({"decisions": []}, candidates=[candidate])
