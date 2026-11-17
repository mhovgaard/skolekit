import json
from datetime import date

import pytest

from skoleklar.triage.consolidation import (
    consolidation_cache_key,
    consolidation_document,
    materialize_consolidation,
    repair_consolidation_output,
)


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


def test_builds_compact_document_and_stable_cache_key() -> None:
    items = [derived_item(2), derived_item(1)]
    document = consolidation_document(items, as_of=date(2026, 8, 24))

    assert [item["item_id"] for item in document["items"]] == [1, 2]
    assert "source_urls" not in document["items"][0]
    assert consolidation_cache_key(items, model="model", document=document) == (
        consolidation_cache_key(items, model="model", document=document)
    )


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


def test_conflicting_group_is_always_review() -> None:
    items = [
        derived_item(1, effective_date="2026-09-03", end_date="2026-09-03"),
        derived_item(2, effective_date="2026-09-04", end_date="2026-09-04"),
    ]
    conflict = canonical() | {
        "effective_date": None,
        "end_date": None,
        "timing_rule": "ambiguous",
    }

    result = materialize_consolidation(
        {
            "groups": [
                {
                    "member_item_ids": [1, 2],
                    "resolution": "conflict",
                    "canonical": conflict,
                    "needs_review": False,
                    "review_reason": "",
                    "reason": "Datoerne er forskellige.",
                }
            ],
            "unclustered_item_ids": [],
        },
        items=items,
    )

    assert result[0]["needs_review"] is True
    assert "Conflicting" in result[0]["review_reason"]


def group(member_ids: list[int]) -> dict:
    return {
        "member_item_ids": member_ids,
        "resolution": "duplicate",
        "canonical": canonical(),
        "needs_review": False,
        "review_reason": "",
        "reason": "Duplicate",
    }


def test_repair_keeps_clean_partition_unchanged() -> None:
    items = [derived_item(1), derived_item(2), derived_item(3)]
    output = {"groups": [group([1, 2])], "unclustered_item_ids": [3]}

    partition, repairs = repair_consolidation_output(output, items=items)

    assert partition == output
    assert set(repairs.values()) == {0}


def test_repair_falls_back_to_unclustered_for_invalid_ids() -> None:
    # The 2026-10-30 cloud sync failed on a partition like this one: the whole
    # sync stopped instead of leaving the doubtful items unmerged.
    items = [derived_item(item_id) for item_id in range(1, 9)]
    output = {
        "groups": [
            group([1, 2]),
            group([2, 3]),
            group([4, 99]),
            group([5, 5]),
            group([6]),
        ],
        "unclustered_item_ids": [1, 7, 7],
    }

    partition, repairs = repair_consolidation_output(output, items=items)
    result = materialize_consolidation(partition, items=items)

    assert partition["groups"] == []
    assert partition["unclustered_item_ids"] == list(range(1, 9))
    assert sorted(i for row in result for i in row["member_item_ids"]) == list(
        range(1, 9)
    )
    assert repairs == {
        "dropped_group_count": 5,
        "unknown_id_count": 1,
        "repeated_id_count": 4,
        "omitted_id_count": 1,
    }


def test_repair_prefers_valid_group_over_repeated_unclustered_id() -> None:
    items = [derived_item(1), derived_item(2), derived_item(3)]
    output = {"groups": [group([1, 2])], "unclustered_item_ids": [2, 3]}

    partition, repairs = repair_consolidation_output(output, items=items)
    result = materialize_consolidation(partition, items=items)

    assert partition == {"groups": [group([1, 2])], "unclustered_item_ids": [3]}
    assert [row["member_item_ids"] for row in result] == [[1, 2], [3]]
    assert repairs["repeated_id_count"] == 1


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


def test_two_members_with_one_stable_key_still_make_a_cache_key() -> None:
    """Nothing makes current stable keys unique; the cache key must not compare None with text."""
    twins = [derived_item(1, stable_key="same", category=None), derived_item(2, stable_key="same", category="trip")]
    document = consolidation_document(twins, as_of=date(2026, 8, 24))
    assert consolidation_cache_key(twins, model="m", document=document)
