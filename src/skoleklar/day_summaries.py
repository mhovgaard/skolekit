"""One child-friendly paragraph per school day, written from the weekly plan.

Each weekly-plan version is summarized once: the plan's general blocks join a
day only when they concern it, and the day's own blocks supply the highlights.
Where the plan names the child, the model sees `[barnet]` instead of the name, so
the child's own group, letters or turn reach the summary without any name.
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

MANIFEST = load_prompt_manifest("day-summary-v3")
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
# Stands for the child the summary is for, wherever the plan names them.
CHILD_MARKER = "[barnet]"
_LETTER = "A-Za-zÆØÅæøåÄÖÜäöüÉéÈè"


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


def child_pattern(child: str | None) -> re.Pattern[str] | None:
    """The child's name (and first name) as a whole word, with a genitive `s`.

    Only as the profile writes it, or in capitals: a name is often a word too, so
    'Bjørn' is the child and 'en bjørn i skoven' an animal, 'Maj' the child and
    '1. maj' the month. A name a teacher writes in lower case stays unmarked.
    """
    full = str(child or "").strip()
    names = list(dict.fromkeys(part for part in (full, full.split(" ")[0]) if part))
    if not names:
        return None
    forms = dict.fromkeys(form for name in sorted(names, key=len, reverse=True) for form in (name, name.upper()))
    alternatives = "|".join(re.escape(form) for form in forms)
    return re.compile(rf"(?<![{_LETTER}])(?:{alternatives})(?=s?(?![{_LETTER}]))")


def mark_child(text: str, pattern: re.Pattern[str] | None) -> str:
    """`text` with the child's name replaced by `[barnet]`: 'Ritas' becomes '[barnet]s'."""
    return pattern.sub(CHILD_MARKER, text) if pattern else text


def _block(plan: dict[str, Any], limit: int, pattern: re.Pattern[str] | None = None) -> dict[str, Any] | None:
    text = str(plan.get("content_text") or "").strip()
    if not text:
        return None
    return {"subject": _subject(plan.get("subject")), "text": mark_child(text, pattern)[:limit]}


def plan_document(plan: dict[str, Any], *, class_label: str = CLASS_LABEL,
                  child: str | None = None, facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The model input for one weekly plan: plan text only, no ids or links.

    `child` is the name of the child the plan is for: wherever the plan names
    them, the model sees `[barnet]` instead. `facts` are the facts derived from
    this plan (`store.plan_facts`); each day lists its own by a short id, which
    `fact_refs` maps back to stable keys.
    """
    pattern = child_pattern(child)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for ref, fact in fact_refs(facts):
        by_day.setdefault(str(fact["effective_date"])[:10], []).append(
            {"id": ref, "type": fact["item_type"], "title": mark_child(str(fact["title"]), pattern)})
    week = plan.get("week") or {}
    days = []
    for day in plan.get("days") or []:
        if not day.get("date"):
            continue
        blocks = [_block(item, BLOCK_TEXT_LIMIT, pattern) for item in day.get("plans") or []]
        days.append({
            "date": str(day["date"])[:10],
            "weekday": str(day.get("weekday") or ""),
            "blocks": [block for block in blocks if block],
            "facts": by_day.get(str(day["date"])[:10], []),
        })
    general = [_block(item, GENERAL_TEXT_LIMIT, pattern) for item in plan.get("general_plans") or []]
    return {
        "class_level": class_level(week.get("class_or_group"), class_label),
        "week": {"number": week.get("number"), "year": week.get("year")},
        "general": [block for block in general if block],
        "days": days,
    }


def fact_refs(facts: list[dict[str, Any]] | None) -> list[tuple[str, dict[str, Any]]]:
    """The short id ('f1') the model reads for each fact; stable keys never reach it."""
    return [(f"f{index}", fact) for index, fact in enumerate(facts or [], 1)]


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DaySummaryInvalid("day summary fields must be text or null")
    text = re.sub(r"\s+", " ", _EMOJI.sub("", value)).strip()
    return text or None


def validate_output(document: dict[str, Any], output: Any, *, child: str | None = None,
                    keys: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Cleaned summaries, one per plan day in order, or DaySummaryInvalid.

    The child is `du`: neither the marker nor `child`'s name may appear.
    """
    pattern = child_pattern(child)
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
            if "barnet]" in f"{headline} {text}".casefold() or (
                    pattern and pattern.search(f"{headline} {text}")):
                raise DaySummaryInvalid("day summary must say du, not the child's marker or name")
        if type(day.get("general_used")) is not bool:
            raise DaySummaryInvalid("general_used must be boolean")
        named = day.get("facts")
        offered = {fact["id"] for fact in source.get("facts", [])}
        if not isinstance(named, list) or not set(named) <= offered:
            raise DaySummaryInvalid("facts must name ids among the day's facts")
        keys = keys or {}
        result.append({"date": source["date"], "headline": headline, "text": text,
                       "general_used": day["general_used"] and text is not None,
                       "fact_keys": sorted({keys[name] for name in named if name in keys})
                       if headline is not None else []})
    return result


def summarize_plan(plan: dict[str, Any], client: Any, *, class_label: str = CLASS_LABEL,
                   child: str | None = None, facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Summaries for one plan, retrying with the validator's fixed feedback."""
    document = plan_document(plan, class_label=class_label, child=child, facts=facts)
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
            days = validate_output(document, response["output"], child=child,
                                   keys={ref: fact["stable_key"] for ref, fact in fact_refs(facts)})
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


def cache_key(record_id: int, content_hash: str, model: str,
              facts: list[dict[str, Any]] | None = None) -> str:
    """One summary per plan record, content, the facts read from it, prompt and model;
    siblings may share a plan."""
    material = {"manifest": MANIFEST.content_hash, "model": model, "record": record_id,
                "content": content_hash,
                "facts": sorted([fact["stable_key"], str(fact["title"]), str(fact["effective_date"])]
                                for fact in facts or [])}
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
        facts = store.plan_facts(plan["version_id"])
        key = cache_key(plan["record_id"], plan["content_hash"], model, facts)
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
            result = summarize_plan(plan["plan"], client, class_label=class_label, child=plan["child"],
                                    facts=facts)
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


def summary_dates(state: dict[str, Any]) -> list[str]:
    """The days a frame can show a summary for, in order.

    The composed week's weekdays, then the parents' board's look-ahead school days
    after the next school day that fall in the following week.
    """
    from skoleklar.dashboard_renderer import focus_date
    from skoleklar.parents_board import lookahead_dates
    dates = [day["date"] for day in state.get("weekdays", [])]
    focus = focus_date(state)
    if focus:
        dates += [value.isoformat() for value in lookahead_dates(date.fromisoformat(focus))
                  if value.isoformat() not in dates]
    return sorted(dates)


def attach_day_summaries(state: dict[str, Any], lookup) -> dict[str, Any]:
    """A copy of `state` with one summary per shown school day and child, in hero order."""
    if not state.get("weekdays"):
        return state
    names = [child["name"] for child in state["hero"]["children"]]
    days = [{"date": value, "children": [{"name": name, **lookup(name, value)} for name in names]}
            for value in summary_dates(state)]
    return {**state, "day_summaries": days}
