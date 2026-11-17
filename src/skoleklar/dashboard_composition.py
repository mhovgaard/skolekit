"""Production composition over the same bounded context used by local skills."""
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta

from skoleklar.dashboard_renderer import weekend_day_title
from skoleklar.dashboard_timing import dashboard_focus, dashboard_weekend_dates
from skoleklar.dashboard_rules import finalize
from skoleklar.dashboard_validation import validate_composition
from skoleklar.triage.prompt_manifest import load_prompt_manifest

MANIFEST = load_prompt_manifest("dashboard-v3")
COMPOSITION_MAX_OUTPUT_TOKENS = 8000
# The board is built elastically from the model's slots, so a rejected attempt is rare.
COMPOSITION_MAX_ATTEMPTS = 3
# Validator messages are fixed text. Python's own errors quote the offending value
# (`Invalid isoformat string: '…'`), so a quote marks text that may come from the
# model or the school, and only the error type is kept.
_FIXED_MESSAGE = re.compile(r"[A-Za-z][A-Za-z0-9_ ,;:/()\[\].-]{2,160}")
_FIELD_KEY = re.compile(r"'([a-z_]{1,40}|fact-\d{1,4})'")
# A fact named by a rule: its export reference and, if well-formed, its board location.
_FACT_NAME = re.compile(
    r"fact-\d{1,4}(?: at (?:hero|weekdays\[\d\]|weekend\[\d\]|weekend_overflow|footer)"
    r"(?:\.[a-z_]{1,10}|\[\d{1,2}\]){0,4})?")


class CompositionFailed(ValueError):
    """Every attempt failed validation; `attempts` has one loggable reason per attempt."""

    def __init__(self, message, attempts):
        super().__init__(message)
        self.attempts = attempts


def named_facts(error):
    """The well-formed fact names a rule violation carries; anything else is dropped."""
    return [name for name in getattr(error, "facts", ()) if _FACT_NAME.fullmatch(str(name))][:6]


def validation_reason(error):
    """Why one attempt was rejected, without model output or school content.

    A broken rule adds the facts it concerns, such as `[fact-21; fact-22 at weekend[1]]`.
    """
    text = str(error)
    if isinstance(error, KeyError) and (match := _FIELD_KEY.fullmatch(text)):
        return f"KeyError: {match.group(1)}"
    if isinstance(error, ValueError) and _FIXED_MESSAGE.fullmatch(text):
        facts = named_facts(error)
        return f"{text} [{'; '.join(facts)}]" if facts else text
    return type(error).__name__


def _valid_participant(value):
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"[^\s.]{1,30} ([^\W\d_])\.", value)
    return bool(match and match.group(1).isupper())


def _activity_label(value):
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%d/%m %H:%M" if "T" in text else "%d/%m")


def _message_items(context):
    """Build the two display-safe message rows without model interpretation."""
    source = context.get("messages", {}).get("items", [])
    if source and not any("participants" in item and "child" in item for item in source):
        return None
    result = []
    for child in context.get("children", []):
        candidates = sorted(
            (item for item in source if item.get("child") == child),
            key=lambda item: (str(item.get("activity", "")), str(item.get("observed_at", ""))),
            reverse=True,
        )
        for candidate in candidates:
            participant = next(
                (value for value in candidate.get("participants", []) if _valid_participant(value)),
                None,
            )
            if participant is None:
                continue
            row = {
                "participant": participant,
                "children": [child],
                "activity": _activity_label(candidate.get("activity", "")),
                "unread": bool(candidate.get("unread", False)),
            }
            for key in ("new_thread", "new_message", "latest_unretrieved", "new"):
                if key in candidate:
                    row[key] = bool(candidate[key])
            result.append(row)
            break
    return result[:2]


def _apply_inbox(messages, context):
    """Replace model message rows and the new count with the context's own values."""
    message_items = _message_items(context)
    if message_items is not None:
        messages["items"] = message_items
    if "new_count" in context.get("messages", {}):
        messages["new_count"] = context["messages"]["new_count"]


def restamp(state, context):
    """A copy of `state` carrying the freshness and inbox values of `context`."""
    result = copy.deepcopy(state)
    result["freshness"] = dict(context["freshness"])
    for key in ("checked", "unread_count", "bodies_retained"):
        result["messages"][key] = context["messages"][key]
    _apply_inbox(result["messages"], context)
    return result


def school_calendar(context):
    """The timing policy's dates for the context's `now`, or None without one."""
    if not context.get("now"):
        return None
    now = datetime.fromisoformat(context["now"].replace("Z", "+00:00"))
    focus = dashboard_focus(now)
    monday = focus.school_date - timedelta(days=focus.school_date.weekday())

    def calendar_day(value):
        entry = {"date": value.isoformat()}
        return {**entry, "day": weekend_day_title(entry)}

    return {
        "mode": focus.mode,
        "week_label": f"UGE {focus.school_date.isocalendar().week}",
        "hero": calendar_day(focus.school_date),
        "weekdays": [calendar_day(monday + timedelta(days=i)) for i in range(5)],
        "weekend": [calendar_day(value) for value in dashboard_weekend_dates(now)],
    }


def composition_key(context, model):
    """What the model output depends on; run ids, freshness times and inbox rows are left out."""
    stable = {key: value for key, value in context.items()
              if key not in {"now", "freshness", "harvest_run_id", "messages", "source_checks", "contract"}}
    stable["status"] = context.get("freshness", {}).get("status")
    stable["source_checks"] = [{key: check.get(key) for key in ("child", "source", "status")}
                               for check in context.get("source_checks", [])]
    if _message_items(context) is None:
        # Without display-safe metadata the model writes the inbox rows itself.
        stable["messages"] = context.get("messages")
    document = {"manifest": MANIFEST.content_hash, "model": model,
                "calendar": school_calendar(context), "context": stable}
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reuse_composition(context, cached, rejected=None):
    """A cached state and audit restamped for `context`, or None if they no longer validate.

    The reason a cached composition no longer validates is appended to `rejected`.
    """
    state = restamp(cached["state"], context)
    audit = {**copy.deepcopy(cached["audit"]),
             "source_updated_at": context["freshness"]["source_updated_at"]}
    try:
        finalize(state, audit, context)
        validate_composition(context, state, audit)
    except (ValueError, KeyError, TypeError) as error:
        if rejected is not None:
            rejected.append(validation_reason(error))
        return None
    return state, audit


def compose(context, client):
    """The board for `context`: the model chooses and words, `build_state` writes the board.

    A rejected attempt is retried with the validator's fixed reason and the facts
    it names; after `COMPOSITION_MAX_ATTEMPTS` the caller falls back to code's own board.
    """
    from skoleklar.dashboard_build import build_state

    validation_errors = []
    attempts = []
    calendar = school_calendar(context)
    for attempt in range(COMPOSITION_MAX_ATTEMPTS):
        document = {"context": context, "calendar": calendar}
        if validation_errors:
            document["validation_feedback"] = (
                "Your previous answers broke these board rules. Correct every one without "
                "undoing earlier corrections: " + " | ".join(validation_errors))
        result = client.generate(document, manifest=MANIFEST,
                                 max_output_tokens=COMPOSITION_MAX_OUTPUT_TOKENS)
        try:
            return build_state(context, result["output"])
        except (ValueError, KeyError, TypeError) as error:
            attempts.append(validation_reason(error))
            if attempt == COMPOSITION_MAX_ATTEMPTS - 1:
                raise CompositionFailed(
                    "Dashboard composition failed validation: "
                    f"{type(error).__name__}: {error}",
                    attempts,
                ) from None
            detail = validation_reason(error)
            if str(error) == "private event row must include its retained bring-item":
                detail += (" A child's required bring-item from a message must be on the board "
                           "whenever that child's message event for the same date is shown: add "
                           "its ref to that event's line or give it its own line.")
            if detail not in validation_errors:
                validation_errors.append(detail)
