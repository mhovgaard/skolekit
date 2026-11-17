from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from typing import Any

from skoleklar import fact as fact_model
from skoleklar.triage.derivation import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    PROVIDER,
    OpenRouterClient,
)
from skoleklar.triage.prompt_manifest import DEFAULT_SLOTS, load_prompt_manifest
from skolekit.records import canonical_hash


CONSOLIDATION_VERSION = "consolidate-v1"
_PACK = load_prompt_manifest(CONSOLIDATION_VERSION, slots=DEFAULT_SLOTS)


def consolidation_manifest():
    """The consolidation's manifest with the household's names in its slots."""
    return load_prompt_manifest(CONSOLIDATION_VERSION)


def consolidation_document(
    items: list[dict[str, Any]], *, as_of: date
) -> dict[str, Any]:
    return {
        "as_of": as_of.isoformat(),
        "items": [
            {
                "item_id": int(item["id"]),
                "child": item["child"],
                "stream": item["stream"],
                "type": item["item_type"],
                "audience": item["audience"],
                "title": item["title"],
                "summary": item["summary"],
                "effective_date": item["effective_date"],
                "end_date": item["end_date"],
                "all_day": bool(item["all_day"]),
                "timing_rule": item["timing_rule"],
                "actionability": item["actionability"],
                "urgency": item["urgency"],
                "urgency_reason": item["urgency_reason"],
                "confidence": float(item["confidence"]),
                "needs_review": bool(item["needs_review"]),
                "review_reason": item["review_reason"],
                "source_types": item.get("source_types", []),
            }
            for item in sorted(items, key=lambda value: int(value["id"]))
        ],
    }


def consolidation_cache_key(
    items: list[dict[str, Any]], *, model: str, document: dict[str, Any]
) -> str:
    return canonical_hash(
        {
            "provider": PROVIDER,
            "model": model,
            "prompt_version": CONSOLIDATION_VERSION,
            "taxonomy_version": _PACK.taxonomy_version,
            "manifest_hash": consolidation_manifest().content_hash,
            "member_stable_keys": sorted(item["stable_key"] for item in items),
            "document_hash": canonical_hash(document),
            # What each member is (skoleklar.fact) is copied into the result, not sent
            # to the model, so a member that now says more must not reuse an older result.
            "member_meaning": sorted(json.dumps([item["stable_key"], fact_model.meaning(item)],
                                                sort_keys=True, ensure_ascii=False)
                                     for item in items),
        }
    )


class OpenRouterConsolidationClient:
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

    def consolidate(self, document: dict[str, Any]) -> dict[str, Any]:
        response = self.client.generate(
            document,
            manifest=consolidation_manifest(),
            max_output_tokens=8000,
        )
        return {
            "response_id": response["response_id"],
            "usage": response["usage"],
            "output": response["output"],
        }


def repair_consolidation_output(
    output: dict[str, Any], *, items: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, int]]:
    """Reduce a model partition to an exact one by falling back to unclustered.

    Any group with an unknown, repeated, or shared ID is dropped, so its members
    stay unmerged. Omitted IDs become unclustered. The result is always lossless
    and never merges anything the model did not cleanly propose.
    """
    input_ids = {int(item["id"]) for item in items}
    groups = [
        (group, [int(value) for value in group["member_item_ids"]])
        for group in output["groups"]
    ]
    raw_unclustered = [int(value) for value in output["unclustered_item_ids"]]
    group_claims = Counter(
        item_id for _, member_ids in groups for item_id in set(member_ids)
    )
    mentions = Counter(
        [item_id for _, member_ids in groups for item_id in member_ids]
        + raw_unclustered
    )

    kept_groups: list[dict[str, Any]] = []
    grouped_ids: set[int] = set()
    for group, member_ids in groups:
        if (
            len(member_ids) < 2
            or len(member_ids) != len(set(member_ids))
            or not set(member_ids) <= input_ids
            or any(group_claims[item_id] > 1 for item_id in member_ids)
        ):
            continue
        kept_groups.append(group)
        grouped_ids.update(member_ids)

    unclustered = sorted(input_ids - grouped_ids)
    repairs = {
        "dropped_group_count": len(groups) - len(kept_groups),
        "unknown_id_count": len(set(mentions) - input_ids),
        "repeated_id_count": sum(1 for count in mentions.values() if count > 1),
        "omitted_id_count": len(input_ids - set(mentions)),
    }
    return {"groups": kept_groups, "unclustered_item_ids": unclustered}, repairs


def materialize_consolidation(
    output: dict[str, Any], *, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    item_by_id = {int(item["id"]): item for item in items}
    input_ids = set(item_by_id)
    used_ids: set[int] = set()
    materialized: list[dict[str, Any]] = []

    for group in output["groups"]:
        member_ids = [int(value) for value in group["member_item_ids"]]
        if len(member_ids) < 2 or len(member_ids) != len(set(member_ids)):
            raise ValueError("A consolidation group must contain unique item IDs")
        _claim_ids(member_ids, input_ids=input_ids, used_ids=used_ids)
        members = [item_by_id[item_id] for item_id in member_ids]
        materialized.append(_materialize_group(group, members=members))

    unclustered_ids = [int(value) for value in output["unclustered_item_ids"]]
    if len(unclustered_ids) != len(set(unclustered_ids)):
        raise ValueError("Unclustered item IDs must be unique")
    _claim_ids(unclustered_ids, input_ids=input_ids, used_ids=used_ids)
    if used_ids != input_ids:
        missing = sorted(input_ids - used_ids)
        raise ValueError(f"Consolidation omitted input item IDs: {missing}")
    materialized.extend(
        _materialize_unchanged(item_by_id[item_id]) for item_id in unclustered_ids
    )
    return sorted(
        materialized,
        key=lambda item: (
            item["effective_date"] or "9999-12-31",
            item["child"],
            min(item["member_item_ids"]),
        ),
    )


def _claim_ids(
    item_ids: list[int], *, input_ids: set[int], used_ids: set[int]
) -> None:
    unknown = set(item_ids) - input_ids
    if unknown:
        raise ValueError(f"Consolidation returned unknown item IDs: {sorted(unknown)}")
    repeated = set(item_ids) & used_ids
    if repeated:
        raise ValueError(f"Consolidation reused item IDs: {sorted(repeated)}")
    used_ids.update(item_ids)


def _materialize_group(
    group: dict[str, Any], *, members: list[dict[str, Any]]
) -> dict[str, Any]:
    canonical = dict(group["canonical"])
    resolution = group["resolution"]
    needs_review = bool(group["needs_review"]) or resolution == "conflict"
    review_reasons = [group["review_reason"]] if group["review_reason"] else []
    review_reasons.extend(
        member["review_reason"]
        for member in members
        if member["needs_review"] and member["review_reason"]
    )
    if any(member["needs_review"] for member in members):
        needs_review = True
    if resolution == "conflict" and not group["review_reason"]:
        review_reasons.append("Conflicting source items require review.")

    member_children = {member["child"] for member in members}
    if canonical["child"] not in member_children:
        needs_review = True
        review_reasons.append("Canonical child scope is not present in group members.")

    if resolution != "conflict":
        member_dates = {member["effective_date"] for member in members}
        member_end_dates = {member["end_date"] for member in members}
        if canonical["effective_date"] not in member_dates:
            needs_review = True
            review_reasons.append("Canonical effective date is not present in members.")
            canonical["effective_date"] = None
            canonical["end_date"] = None
            canonical["timing_rule"] = "ambiguous"
        elif canonical["end_date"] not in member_end_dates:
            needs_review = True
            review_reasons.append("Canonical end date is not present in members.")
            canonical["end_date"] = canonical["effective_date"]

    for field in ("effective_date", "end_date"):
        if not _valid_date(canonical[field]):
            needs_review = True
            review_reasons.append(f"Canonical {field} is not a valid ISO date.")
            canonical[field] = None

    member_ids = sorted(int(member["id"]) for member in members)
    stable_key = _group_stable_key(canonical, members=members)
    item_type = canonical.pop("type")
    return {
        **canonical,
        **_group_shape(item_type, members),
        "item_type": item_type,
        "stable_key": stable_key,
        "resolution": resolution,
        "member_item_ids": member_ids,
        "needs_review": needs_review,
        "review_reason": " ".join(dict.fromkeys(review_reasons)).strip(),
        "reason": group["reason"],
        "source_urls": sorted(
            {
                url
                for member in members
                for url in member.get("source_urls", [])
            }
        ),
        "source_types": sorted(
            {
                source_type
                for member in members
                for source_type in member.get("source_types", [])
            }
        ),
        "harvest_run_id": max(int(member["harvest_run_id"]) for member in members),
    }


def _group_shape(item_type: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    """What a merged item is, from its members (`skoleklar.fact.merge`)."""
    return fact_model.merge(item_type, members)


def _materialize_unchanged(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in (*fact_model.CORE, *fact_model.PROVENANCE)} \
        | fact_model.meaning(item) | {
        "resolution": "unchanged",
        "member_item_ids": [int(item["id"])],
        "reason": "No semantic duplicate was identified.",
    }


def _group_stable_key(
    canonical: dict[str, Any], *, members: list[dict[str, Any]]
) -> str:
    material = {
        "members": sorted(member["stable_key"] for member in members),
        "child": canonical["child"],
        "type": canonical["type"],
        "effective_date": canonical["effective_date"],
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _valid_date(value: str | None) -> bool:
    if value is None:
        return True
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False
