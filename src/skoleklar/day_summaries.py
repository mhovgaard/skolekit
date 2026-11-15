"""One child-friendly paragraph per school day, written from the weekly plan.

Each weekly-plan version is summarized once: the plan's general blocks join a
day only when they concern it, and the day's own blocks supply the highlights.
The result is stored per plan version and attached to the board state after
composition, so it never changes what the composition model sees or costs.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any, Callable

from skoleklar.triage.derivation import OpenRouterGenerationError, OpenRouterHTTPError
from skoleklar.triage.prompt_manifest import load_prompt_manifest
from skoleklar.household import load_site_profile
from skolekit.school import CLASS_LABEL

MANIFEST = load_prompt_manifest("day-summary-v1")
MAX_ATTEMPTS = 3
MAX_OUTPUT_TOKENS = 4000
DEFAULT_PLAN_LIMIT = 6
HEADLINE_LIMIT = 48
TEXT_LIMIT = 480
GENERAL_TEXT_LIMIT = 6000
BLOCK_TEXT_LIMIT = 3000
STATUSES = ("ok", "no_content", "not_published", "unavailable")
# SkoleIntra's label for a block without a subject.
UNNAMED_SUBJECTS = {"uden angivelse af fag", "diverse", ""}
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF⌀-⏿☀-➿⬀-⯿"
    "︎️‍⃣]"
)
_RELATIVE_TIME = re.compile(r"\b(i dag|i morgen|i aften|i går)\b", re.IGNORECASE)
_STOP_ERRORS = {401, 402, 403}


class DaySummaryInvalid(ValueError):
    """The model's day summaries broke the contract; messages are fixed text."""


def class_level(value: Any, label: str = CLASS_LABEL) -> str:
    """The plan's class as the school writes it ([classes] label): '2' is '2. klasse'."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{1,2}", text):
        return label.format(level=int(text))
    return text[:30] or "ukendt klasse"


def _subject(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if text.casefold() in UNNAMED_SUBJECTS else text


def _block(plan: dict[str, Any], limit: int) -> dict[str, Any] | None:
    text = str(plan.get("content_text") or "").strip()
    if not text:
        return None
    return {"subject": _subject(plan.get("subject")), "text": text[:limit]}


def plan_document(plan: dict[str, Any], *, class_label: str = CLASS_LABEL) -> dict[str, Any]:
    """The model input for one weekly plan: plan text only, no ids, links or names."""
    week = plan.get("week") or {}
    days = []
    for day in plan.get("days") or []:
        if not day.get("date"):
            continue
        blocks = [_block(item, BLOCK_TEXT_LIMIT) for item in day.get("plans") or []]
        days.append({
            "date": str(day["date"])[:10],
            "weekday": str(day.get("weekday") or ""),
            "blocks": [block for block in blocks if block],
        })
    general = [_block(item, GENERAL_TEXT_LIMIT) for item in plan.get("general_plans") or []]
    return {
        "class_level": class_level(week.get("class_or_group"), class_label),
        "week": {"number": week.get("number"), "year": week.get("year")},
        "general": [block for block in general if block],
        "days": days,
    }


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DaySummaryInvalid("day summary fields must be text or null")
    text = re.sub(r"\s+", " ", _EMOJI.sub("", value)).strip()
    return text or None


def validate_output(document: dict[str, Any], output: Any) -> list[dict[str, Any]]:
    """Cleaned summaries, one per plan day in order, or DaySummaryInvalid."""
    days = output.get("days") if isinstance(output, dict) else None
    if not isinstance(days, list) or [d.get("date") if isinstance(d, dict) else None
                                      for d in days] != [d["date"] for d in document["days"]]:
        raise DaySummaryInvalid("day summaries must cover every plan day in order")
    result = []
    for source, day in zip(document["days"], days):
        headline, text = _clean(day.get("headline")), _clean(day.get("text"))
        if (headline is None) != (text is None):
            raise DaySummaryInvalid("headline and text must both be set or both be null")
        if text is None and source["blocks"]:
            raise DaySummaryInvalid("a day with plan content needs a summary")
        if text is not None:
            if len(headline) > HEADLINE_LIMIT or len(text) > TEXT_LIMIT:
                raise DaySummaryInvalid("day summary is too long")
            if _RELATIVE_TIME.search(f"{headline} {text}"):
                raise DaySummaryInvalid("day summary must not use relative time words")
        if type(day.get("general_used")) is not bool:
            raise DaySummaryInvalid("general_used must be boolean")
        result.append({"date": source["date"], "headline": headline, "text": text,
                       "general_used": day["general_used"] and text is not None})
    return result


def summarize_plan(plan: dict[str, Any], client: Any, *,
                   class_label: str = CLASS_LABEL) -> dict[str, Any]:
    """Summaries for one plan, retrying with the validator's fixed feedback."""
    document = plan_document(plan, class_label=class_label)
    errors: list[str] = []
    usage: dict[str, Any] = {}
    for attempt in range(MAX_ATTEMPTS):
        request = dict(document)
        if errors:
            request["validation_feedback"] = (
                "The previous response broke the contract. Correct every error: "
                + " | ".join(errors)
            )
        response = client.generate(request, manifest=MANIFEST,
                                   max_output_tokens=MAX_OUTPUT_TOKENS)
        usage = _add_usage(usage, response.get("usage") or {})
        try:
            days = validate_output(document, response["output"])
        except DaySummaryInvalid as error:
            if str(error) not in errors:
                errors.append(str(error))
            if attempt == MAX_ATTEMPTS - 1:
                raise DaySummaryInvalid(str(error)) from None
            continue
        return {"days": days, "response_id": response.get("response_id") or "",
                "usage": usage, "attempts": attempt + 1}
    raise AssertionError("unreachable")


def _add_usage(total: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    result = dict(total)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
        if isinstance(usage.get(key), (int, float)):
            result[key] = result.get(key, 0) + usage[key]
    return result


def cache_key(record_id: int, content_hash: str, model: str) -> str:
    """One summary per plan record, content, prompt and model; siblings may share a plan."""
    material = {"manifest": MANIFEST.content_hash, "model": model, "record": record_id,
                "content": content_hash}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _weeks_around(today: date) -> list[tuple[int, int]]:
    """This ISO week and the next, so the board's weeks have plans to summarize."""
    weeks = []
    for value in (today, today + timedelta(days=7)):
        iso = value.isocalendar()
        weeks.append((iso.year, iso.week))
    return weeks


def generate_day_summaries(store, client, *, model: str, today: date,
                           limit: int = DEFAULT_PLAN_LIMIT, dry_run: bool = False) -> dict[str, Any]:
    """Summarize the plans seen in the latest harvest and this and next week's plans.

    A plan version already summarized with this manifest and model is skipped. A
    failed plan is recorded and retried on the next run; authentication and billing
    errors stop the run. Results are counts and fixed error codes only.
    """
    plans = store.weekly_plan_versions(weeks=_weeks_around(today))
    class_label = load_site_profile().school.class_label
    summary = {"candidate_count": len(plans), "reused_count": 0, "generated_count": 0,
               "failed_count": 0, "skipped_count": 0, "failures": [], "plans": []}
    for plan in plans:
        key = cache_key(plan["record_id"], plan["content_hash"], model)
        existing = store.day_summary_run(key)
        label = {"child": plan["child"], "week": plan["source_key"]}
        if existing is not None and existing["status"] == "complete":
            summary["reused_count"] += 1
            summary["plans"].append({**label, "result": "reused"})
            continue
        if dry_run or summary["generated_count"] + summary["failed_count"] >= limit:
            summary["skipped_count"] += 1
            summary["plans"].append({**label, "result": "pending"})
            continue
        run_id = store.begin_day_summary_run(
            cache_key=key, record_id=plan["record_id"], version_id=plan["version_id"],
            child=plan["child"], content_hash=plan["content_hash"], provider="openrouter",
            model=model, manifest_id=MANIFEST.manifest_id, manifest_hash=MANIFEST.content_hash)
        try:
            result = summarize_plan(plan["plan"], client, class_label=class_label)
        except OpenRouterHTTPError as error:
            store.fail_day_summary_run(run_id, f"provider_http_{error.status_code}")
            if error.status_code in _STOP_ERRORS:
                raise
            code = f"provider_http_{error.status_code}"
        except OpenRouterGenerationError as error:
            code = f"{error.stage}:{error.error_code}"
            store.fail_day_summary_run(run_id, code)
        except DaySummaryInvalid as error:
            code = str(error)
            store.fail_day_summary_run(run_id, code)
        except Exception as error:  # A plan's failure must not stop the others.
            code = type(error).__name__
            store.fail_day_summary_run(run_id, code)
        else:
            store.complete_day_summary_run(run_id, days=result["days"],
                                           response_id=result["response_id"],
                                           usage=result["usage"])
            summary["generated_count"] += 1
            summary["plans"].append({**label, "result": "generated",
                                     "attempts": result["attempts"]})
            continue
        summary["failed_count"] += 1
        summary["failures"].append(code)
        summary["plans"].append({**label, "result": "failed", "error": code})
    return summary


def summary_lookup(store) -> Callable[[str, str], dict[str, Any]]:
    """`lookup(child, iso_date)` → the day's board entry, reading each plan once."""
    weeks: dict[tuple[str, int, int], Any] = {}

    def lookup(child: str, value: str) -> dict[str, Any]:
        iso = date.fromisoformat(value).isocalendar()
        key = (child, iso.year, iso.week)
        if key not in weeks:
            weeks[key] = store.day_summary_days(child, iso.year, iso.week)
        plan = weeks[key]
        if plan is None:
            return {"status": "not_published", "headline": None, "text": None}
        if plan["days"] is None:
            return {"status": "unavailable", "headline": None, "text": None}
        row = plan["days"].get(value)
        if row is None or row["text"] is None:
            return {"status": "no_content", "headline": None, "text": None}
        return {"status": "ok", "headline": row["headline"], "text": row["text"]}
    return lookup


def attach_day_summaries(state: dict[str, Any], lookup) -> dict[str, Any]:
    """A copy of `state` with one summary per weekday and child, in hero order."""
    if state.get("schema_version") != 2 or not state.get("weekdays"):
        return state
    names = [child["name"] for child in state["hero"]["children"]]
    days = [{"date": day["date"],
             "children": [{"name": name, **lookup(name, day["date"])} for name in names]}
            for day in state["weekdays"]]
    return {**state, "day_summaries": days}
