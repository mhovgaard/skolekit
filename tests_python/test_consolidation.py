import json
from datetime import date

import pytest

from skoleklar.triage.consolidation import (
    consolidation_cache_key,
    consolidation_document,
    materialize_consolidation,
    repair_consolidation_output,
)

# This refactor's core set: `pytest -m pipeline` (docs/maintenance.md).
pytestmark = pytest.mark.pipeline


def derived_item(item_id: int, **overrides: object) -> dict:
    value = {
        "id": item_id,
        "stable_key": f"stable-{item_id}",
        "stream": "context",
        "item_type": "context",
        "audience": "family",
        "child": "Alfa",
        "title": "Pinny var med hjemme",
        "summary": "Alfa havde klassebamsen Pinny med hjem.",
        "effective_date": None,
        "end_date": None,
        "all_day": True,
        "timing_rule": "no_derived_date",
        "actionability": "informational",
        "urgency": "routine",
        "urgency_reason": "Klasseinformation.",
        "confidence": 0.9,
        "needs_review": False,
        "review_reason": "",
        "source_urls": [f"https://x.skoleintra.dk/item/{item_id}"],
        "source_types": ["weekly_plan"],
        "harvest_run_id": 9,
    }
    value.update(overrides)
    return value


def canonical() -> dict:
    return {
        "stream": "context",
        "type": "context",
        "audience": "family",
        "child": "Alfa",
        "title": "Alfa havde Pinny med hjem",
        "summary": "Alfa havde klassebamsen Pinny med hjem som del af klassens ordning.",
        "effective_date": None,
        "end_date": None,
        "all_day": True,
        "timing_rule": "no_derived_date",
        "actionability": "informational",
        "urgency": "routine",
        "urgency_reason": "Det er én gentaget status.",
        "confidence": 0.95,
    }


def test_materializes_duplicate_group_and_unions_provenance() -> None:
    items = [derived_item(1), derived_item(2, source_types=["messages"])]
    output = {
        "groups": [
            {
                "member_item_ids": [2, 1],
                "resolution": "duplicate",
                "canonical": canonical(),
                "needs_review": False,
                "review_reason": "",
                "reason": "Samme barn, bamse og hjemtagning.",
            }
        ],
        "unclustered_item_ids": [],
    }

    result = materialize_consolidation(output, items=items)

    assert len(result) == 1
    assert result[0]["member_item_ids"] == [1, 2]
    assert result[0]["item_type"] == "context"
    assert "type" not in result[0]
    assert result[0]["source_types"] == ["messages", "weekly_plan"]
    assert len(result[0]["source_urls"]) == 2


def test_requires_lossless_exact_partition() -> None:
    items = [derived_item(1), derived_item(2)]

    with pytest.raises(ValueError, match="omitted"):
        materialize_consolidation(
            {"groups": [], "unclustered_item_ids": [1]}, items=items
        )
    with pytest.raises(ValueError, match="reused"):
        materialize_consolidation(
            {
                "groups": [
                    {
                        "member_item_ids": [1, 2],
                        "resolution": "duplicate",
                        "canonical": canonical(),
                        "needs_review": False,
                        "review_reason": "",
                        "reason": "Duplicate",
                    }
                ],
                "unclustered_item_ids": [2],
            },
            items=items,
        )


def group(member_ids: list[int]) -> dict:
    return {
        "member_item_ids": member_ids,
        "resolution": "duplicate",
        "canonical": canonical(),
        "needs_review": False,
        "review_reason": "",
        "reason": "Duplicate",
    }


def test_what_items_are_survives_consolidation_and_keys_its_cache() -> None:
    """derive-v10's fields come from the members, never from the consolidation model."""
    party = dict(stream="calendar", item_type="event", category="party", moves_time=None, recurring=False)
    items = [derived_item(1, **party), derived_item(2, **party),
             derived_item(3, stream="calendar", item_type="schedule_change", category=None,
                          moves_time=True, recurring=False),
             derived_item(4)]  # derived before v10: says nothing
    group = {"member_item_ids": [1, 2], "resolution": "duplicate",
             "canonical": canonical() | {"stream": "calendar", "type": "event"},
             "needs_review": False, "review_reason": "", "reason": "Samme fest."}
    result = materialize_consolidation({"groups": [group], "unclustered_item_ids": [3, 4]}, items=items)
    by_type = {entry["item_type"]: entry for entry in result}
    assert (by_type["event"]["category"], by_type["event"]["recurring"]) == ("party", False)
    assert by_type["schedule_change"]["moves_time"] is True
    assert not {"category", "moves_time", "recurring"} & set(by_type["context"])

    # Members that disagree leave the category open; the model is never asked.
    mixed = [derived_item(1, **party), derived_item(2, **(party | {"category": "trip"}))]
    [merged] = materialize_consolidation({"groups": [group], "unclustered_item_ids": []}, items=mixed)
    assert merged["category"] is None

    document = consolidation_document(items, as_of=date(2026, 8, 24))
    assert "category" not in json.dumps(document)
    before = [derived_item(1, stream="calendar", item_type="event"), derived_item(2)]
    after = [derived_item(1, **party), derived_item(2)]
    assert consolidation_cache_key(before, model="m", document=document) != \
        consolidation_cache_key(after, model="m", document=document)


