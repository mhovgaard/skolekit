"""One fact, from extraction to pixels.

A fact is what a source says the family has to know or do: homework, something
to bring, an event, a changed time, a reply. Its meaning is decided once, by the
extractor that reads the source (`derive-v11` for published pages, the restricted
`message-events-v4` for messages), and every later stage passes it on as it is:
the store, consolidation, the brief, the board's context, the phone and the MCP.
Nothing downstream reads a fact's words to work out what it is.

The fields are defined here and nowhere else. A stage that copies a fact copies
`CORE` and `MEANING`; adding a field to `MEANING` (and to the extractor that
fills it) is all it takes for the field to reach the board.

`kind` is what a fact is on the board, from its typed fields alone.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# The contract every current fact carries. A fact derived under an older one is
# re-derived by the scan (`store.contract_candidates`), never read differently.
CONTRACT = "fact-v1"

# The columns every fact has, under the store's names (`item_type` is the fact's
# type; the brief and the board call it `type`).
CORE = ("stable_key", "stream", "item_type", "audience", "child", "title", "summary",
        "effective_date", "end_date", "all_day", "timing_rule", "actionability", "urgency",
        "urgency_reason", "confidence", "needs_review", "review_reason")
# Each column's type, for a result schema such as the MCP's.
CORE_TYPES: dict[str, Any] = {
    "id": int, "stable_key": str, "stream": str | None, "item_type": str | None, "type": str | None,
    "audience": str | None, "child": str | None, "title": str | None, "summary": str | None,
    "effective_date": str | None, "end_date": str | None, "all_day": bool, "timing_rule": str | None,
    "actionability": str | None, "urgency": str | None, "urgency_reason": str | None,
    "confidence": float | str | None, "needs_review": bool | int | None, "review_reason": str | None,
    "acknowledged_at": str | None, "completed_at": str | None,
}
# What each reader sees of a fact's columns, besides its meaning (`public`): the
# brief (and the phone, which reads it), the board's context and the week view.
BRIEF_CORE = ("id", "stable_key", "stream", "child", "title", "summary", "effective_date", "end_date",
              "actionability", "urgency", "confidence", "needs_review")
BOARD_CORE = ("type", "child", "title", "summary", "effective_date", "end_date", "actionability",
              "urgency", "confidence", "needs_review")
WEEK_CORE = ("stable_key", "child", "item_type", "title", "summary", "effective_date", "end_date",
             "all_day", "actionability", "urgency", "needs_review", "acknowledged_at", "completed_at")
# Where a fact came from, kept beside it but never sent to a model or the board.
PROVENANCE = ("source_urls", "source_types", "harvest_run_id")
# The workflow state a fact's stable key carries across re-derivation.
WORKFLOW = ("first_delivered_at", "last_delivered_at", "acknowledged_at", "completed_at",
            "dismissed_at")


@dataclass(frozen=True)
class Field:
    """One typed field of what a fact is.

    `types` are the fact types it means something for; on any other type it holds
    `empty`. `merge` is how consolidation joins the members of a merged fact:
    `agree` keeps a value all members share, `any` is true when one member says
    so, `all` only when every member does, `union` joins lists.
    """
    name: str
    kind: type
    types: frozenset[str]
    empty: Any
    merge: str
    # The values allowed, for every type or per type.
    values: frozenset[Any] | dict[str, frozenset[Any]] | None = None
    limit: int = 80

    def holds(self, value: Any, fact_type: str | None) -> bool:
        if fact_type not in self.types or value is None:
            return False
        if self.kind is list:
            return (isinstance(value, list) and 0 < len(value) <= 8
                    and all(_short_text(item, self.limit) for item in value))
        if self.kind is str:
            allowed = self.values.get(fact_type) if isinstance(self.values, dict) else self.values
            return _short_text(value, self.limit) and (allowed is None or value in allowed)
        return isinstance(value, self.kind)

    def normal(self, value: Any, fact_type: str | None) -> Any:
        """`value` where it means something for `fact_type`, else `empty`."""
        if not self.holds(value, fact_type):
            return list(self.empty) if isinstance(self.empty, list) else self.empty
        return [" ".join(item.split()) for item in value] if self.kind is list else value


def _short_text(value: Any, limit: int) -> bool:
    return (isinstance(value, str) and 0 < len(value.strip()) <= limit
            and not any(ord(c) < 32 for c in value))


CATEGORIES = frozenset({"trip", "party", "meeting", "birthday", "other"})
ACTIVITY_KINDS = frozenset({"birthday_party", "class_trip", "class_event", "social_activity"})

MEANING = (
    # An event's kind: an outing, a celebration, a meeting, or another occurrence.
    # A parent action is a meeting when it means attending one, else nothing.
    Field("category", str, frozenset({"event", "parent_action"}), None, "agree",
          {"event": CATEGORIES, "parent_action": frozenset({"meeting"})}),
    # Whether a schedule change moves when the day or an activity starts or ends.
    Field("moves_time", bool, frozenset({"schedule_change"}), None, "any"),
    # An event the class has every week, such as morning singing.
    Field("recurring", bool, frozenset({"event", "routine"}), False, "all"),
    # The school subject the item belongs to, as the source names it.
    Field("school_subject", str, frozenset({"homework", "bring", "lesson_preview", "schedule_change",
                                     "substitute"}), None, "agree", limit=40),
    # The substitute teacher's name.
    Field("teacher", str, frozenset({"substitute"}), None, "agree", limit=60),
    # The things to bring, each a few words.
    Field("bring_items", list, frozenset({"bring"}), [], "union", limit=60),
    # Whether the things to bring are rain clothes or boots.
    Field("rain_gear", bool, frozenset({"bring"}), False, "any"),
    # A message event's category (message-events-v4).
    Field("activity_kind", str, frozenset({"event"}), None, "agree", ACTIVITY_KINDS),
    # A birthday party's birthday child, by first name (message-events-v4).
    Field("celebrant", str, frozenset({"event"}), None, "agree", limit=24),
)


def names() -> tuple[str, ...]:
    """The meaning fields' names; read from `MEANING` each time, so a field added there
    is carried everywhere without another edit."""
    return tuple(field.name for field in MEANING)


def fact_type(fact: dict[str, Any]) -> str | None:
    return fact.get("item_type", fact.get("type"))


def normalize(fact: dict[str, Any]) -> dict[str, Any]:
    """`fact` with every meaning field present and valid for its type, in place.

    A value on a type it does not belong to, or of the wrong shape, becomes the
    field's empty value: these are what a fact is, so a wrong one is dropped
    rather than guessed at. Stamps the current `contract`.
    """
    kind = fact_type(fact)
    for field in MEANING:
        fact[field.name] = field.normal(fact.get(field.name), kind)
    fact["contract"] = CONTRACT
    return fact


def meaning(source: dict[str, Any]) -> dict[str, Any]:
    """The meaning fields and contract of a fact or a stored payload, as they are."""
    result = {name: source[name] for name in names() if name in source}
    if "contract" in source:
        result["contract"] = source["contract"]
    return result


def current(fact: dict[str, Any]) -> bool:
    """Whether `fact` was made under the current contract."""
    return fact.get("contract") == CONTRACT


def merge(item_type: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    """What a merged fact is, from its members of the same type.

    Members of another type say nothing about the merged fact's fields. The
    result carries the current contract only when every member did.
    """
    said = [member for member in members if fact_type(member) == item_type]
    merged: dict[str, Any] = {}
    for field in MEANING:
        values = [field.normal(member.get(field.name), item_type) for member in said]
        if field.merge == "agree":
            distinct = {json.dumps(value) for value in values}
            value = values[0] if len(distinct) == 1 else field.empty
        elif field.merge == "any":
            known = [value for value in values if isinstance(value, bool)]
            value = any(known) if known else field.empty
        elif field.merge == "all":
            value = bool(values) and all(value is True for value in values)
        else:
            value = list(dict.fromkeys(item for items in values for item in items))[:8]
        merged[field.name] = field.normal(value, item_type)
    merged["contract"] = CONTRACT if members and all(current(member) for member in members) else None
    return merged


def core_annotations(keys: tuple[str, ...]) -> dict[str, Any]:
    """The columns `keys` as types, for a result schema such as the MCP's."""
    return {key: CORE_TYPES[key] for key in keys}


def annotations() -> dict[str, Any]:
    """The meaning fields as types, for a result schema such as the MCP's."""
    as_type = {str: str | None, bool: bool | None, list: list[str]}
    return {field.name: as_type[field.kind] for field in MEANING} | {"contract": str | None}


def public(item: dict[str, Any]) -> dict[str, Any]:
    """A fact's type and meaning as the brief, the board's context and the MCP read them.

    Every field is present, empty where it says nothing; `contract` says which
    contract made the fact (None before this model).
    """
    kind = fact_type(item)
    return {"type": kind, **{field.name: field.normal(item.get(field.name), kind) for field in MEANING},
            "contract": item.get("contract")}


# --- What a fact is on the board -------------------------------------------------------

KINDS = ("homework", "bring", "optional", "deadline", "time_change", "change", "substitute",
         "trip", "party", "meeting", "event", "info", "routine", "more")
EVENT_KINDS = ("trip", "party", "meeting", "event")
# When a line joins facts of several kinds, the lowest rank names the line. A
# routine ranks last, so a line that joins it with anything else is never hidden.
RANK = {"homework": 0, "trip": 1, "party": 1, "meeting": 1, "event": 1, "deadline": 2,
        "time_change": 3, "change": 4, "bring": 5, "optional": 6, "substitute": 7, "info": 8,
        "more": 9, "routine": 10}
_CATEGORY_KINDS = {"trip": "trip", "party": "party", "meeting": "meeting", "other": "event",
                   "birthday": "info"}
ACTIVITY_BOARD_KINDS = {"class_trip": "trip", "birthday_party": "party",
                        "class_event": "event", "social_activity": "event"}


def kind(fact: dict[str, Any]) -> str:
    """What one fact is on the board, from its type and typed fields alone.

    An owner's confirmed category comes first, then a message's category, then
    the derivation's. A fact without them (`category` null) is a plain event.
    """
    item_type, actionability = fact_type(fact), fact.get("actionability")
    if item_type == "homework":
        return "homework"
    if item_type == "bring":
        return "optional" if actionability == "recommended" else "bring"
    if item_type == "parent_action":
        if fact.get("category") == "meeting":
            return "meeting"
        return "optional" if actionability == "recommended" else "deadline"
    if item_type == "schedule_change":
        return "time_change" if fact.get("moves_time") is True else "change"
    if item_type == "substitute":
        return "substitute"
    if item_type == "event":
        if fact.get("recurring") is True and not fact.get("cancelled"):
            return "routine"
        activity = fact.get("confirmed_activity_kind") or fact.get("activity_kind")
        if activity in ACTIVITY_BOARD_KINDS:
            return ACTIVITY_BOARD_KINDS[activity]
        return _CATEGORY_KINDS.get(fact.get("category"), "event")
    return "info"


def line_kind(facts: list[dict[str, Any]]) -> str:
    """The kind of a line that shows `facts`: homework first, then events, then the rest."""
    return min((kind(fact) for fact in facts), key=lambda value: RANK[value], default="info")
