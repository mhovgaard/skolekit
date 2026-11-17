from datetime import UTC, date, datetime

from skoleklar.contracts import validate_contract
from skoleklar.delivery_manifest import (
    build_delivery_manifest,
    delivery_items_from_snapshot,
)


def item(**overrides: object) -> dict:
    value = {
        "id": 1,
        "stable_key": "event:rita:museum:2026-08-26",
        "stream": "calendar",
        "item_type": "event",
        "audience": "child",
        "child": "Beta",
        "title": "Museum trip",
        "summary": "Meet at school with a packed lunch.",
        "effective_date": "2026-08-26",
        "end_date": "2026-08-26",
        "all_day": True,
        "timing_rule": "explicit_date",
        "actionability": "informational",
        "urgency": "attention",
        "urgency_reason": "Changed school day.",
        "confidence": 0.96,
        "needs_review": False,
        "review_reason": "",
        "source_urls": ["https://x.skoleintra.dk/item/1"],
        "source_types": ["calendar"],
        "harvest_run_id": 9,
        "first_delivered_at": None,
        "acknowledged_at": None,
        "completed_at": None,
        "dismissed_at": None,
    }
    value.update(overrides)
    return value


def test_builds_deterministic_valid_manifest_and_routes_native_items() -> None:
    generated_at = datetime(2026, 8, 25, 18, 31, 42, tzinfo=UTC)
    items = [
        item(),
        item(
            id=2,
            stable_key="bring:rita:book:2026-08-26",
            stream="actions",
            item_type="bring",
            title="Bring mathematics book home",
            summary="Needed for pages 42–44.",
        ),
    ]

    first = build_delivery_manifest(
        items, target_date=date(2026, 8, 25), generated_at=generated_at
    )
    second = build_delivery_manifest(
        items, target_date=date(2026, 8, 25), generated_at=generated_at
    )

    validate_contract("manifest-v2", first)
    assert first == second
    assert first["calendar"][0]["title"] == "Beta · Museum trip"
    assert first["reminders"][0]["list"] == "School · Bring home"
    assert first["manifest_id"].endswith("-" + second["manifest_id"].split("-")[-1])


def test_changed_content_changes_revision() -> None:
    original = build_delivery_manifest(
        [item()],
        target_date=date(2026, 8, 25),
        generated_at=datetime(2026, 8, 25, 18, tzinfo=UTC),
    )
    changed = build_delivery_manifest(
        [item(summary="Meet at 08:15 with a packed lunch.")],
        target_date=date(2026, 8, 25),
        generated_at=datetime(2026, 8, 25, 19, tzinfo=UTC),
    )

    assert original["calendar"][0]["stable_key"] == changed["calendar"][0]["stable_key"]
    assert original["calendar"][0]["revision"] != changed["calendar"][0]["revision"]


def test_ambiguous_and_timed_items_are_review_only() -> None:
    manifest = build_delivery_manifest(
        [
            item(all_day=False),
            item(
                id=2,
                stable_key="homework:rita:unknown",
                item_type="homework",
                stream="actions",
                effective_date=None,
                end_date=None,
            ),
        ],
        target_date=date(2026, 8, 25),
        generated_at=datetime(2026, 8, 25, 18, tzinfo=UTC),
    )

    assert manifest["calendar"] == []
    assert manifest["reminders"] == []
    assert len(manifest["review"]) == 2
    assert "explicit start and end times" in manifest["review"][0]["reason"]


def test_completed_items_are_not_republished() -> None:
    manifest = build_delivery_manifest(
        [item(completed_at="2026-08-25T18:00:00Z")],
        target_date=date(2026, 8, 25),
        generated_at=datetime(2026, 8, 25, 18, tzinfo=UTC),
    )

    assert manifest["calendar"] == []
    assert manifest["brief"]["signal"] == "green"


def test_items_that_ended_before_the_manifest_day_are_left_out() -> None:
    manifest = build_delivery_manifest(
        [
            item(id=1, stable_key="event:past", effective_date="2026-08-24", end_date="2026-08-24"),
            item(id=2, stable_key="event:running", effective_date="2026-08-24", end_date="2026-08-26"),
            item(id=3, stable_key="event:today", effective_date="2026-08-25", end_date=None),
            item(id=4, stable_key="bring:overdue", item_type="bring", stream="actions",
                 effective_date="2026-08-24", end_date="2026-08-30"),
            item(id=5, stable_key="bring:today", item_type="bring", stream="actions",
                 effective_date="2026-08-25", end_date=None),
            item(id=6, stable_key="review:past", needs_review=True, review_reason="Unclear.",
                 effective_date="2026-08-20", end_date="2026-08-20"),
            item(id=7, stable_key="review:undated", needs_review=True, review_reason="Unclear.",
                 effective_date=None, end_date=None),
        ],
        target_date=date(2026, 8, 25),
        generated_at=datetime(2026, 8, 25, 18, tzinfo=UTC),
    )

    assert [value["stable_key"] for value in manifest["calendar"]] == ["event:running", "event:today"]
    assert [value["stable_key"] for value in manifest["reminders"]] == ["bring:today"]
    assert [value["stable_key"] for value in manifest["review"]] == ["review:undated"]


def test_stale_consolidation_falls_back_to_current_derivations() -> None:
    current = [item(id=2, stable_key="current")]

    class Store:
        def current_derived_items(self) -> list[dict]:
            return current

        def consolidation_report(self) -> dict:
            return {
                "harvest_run_id": 9,
                "items": [
                    {
                        **item(id=10, stable_key="old"),
                        "member_item_ids": [1],
                    }
                ],
            }

        def latest_completed_run_id(self) -> int:
            return 9

    assert delivery_items_from_snapshot(Store()) == current


def test_each_entry_says_what_it_is_in_typed_fields() -> None:
    """The phone reads the same typed facts as the board (skoleklar.fact), never a title."""
    bring = item(stable_key="bring-1", item_type="bring", title="Medbring gave", bring_items=["gave"],
                 actionability="required")
    trip = item(stable_key="trip-1", item_type="event", title="Skovdag", category="trip")
    manifest = build_delivery_manifest([bring, trip], target_date=date(2026, 8, 24),
                                       generated_at=datetime(2026, 8, 24, 6, tzinfo=UTC))
    [reminder] = manifest["reminders"]
    assert (reminder["kind"], reminder["fact"]["type"], reminder["fact"]["bring_items"]) == ("bring", "bring", ["gave"])
    [event] = manifest["calendar"]
    assert (event["kind"], event["fact"]["category"]) == ("trip", "trip")
    assert "contract" not in event["fact"]
    validate_contract("manifest-v2", manifest)
