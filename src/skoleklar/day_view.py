"""The children's view of one school day: the timetable, its changes and what to remember.

Before school (until 08:30) the panel shows the children their day, and after
school (15:00–19:00, Sunday to Thursday) the next one. The parents' board shows
the rest of the time. Code builds the view from the stored calendar lesson slots;
no model call is involved. The remember list, weather and weekly-plan summary
come from what the frame already carries (the hero, `day_weather` and
`day_summaries`).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Iterable

from skoleklar.dashboard_timing import ZONE, dashboard_focus
from skolekit.wording import DANISH

# School-day mornings until this time show today's view.
MORNING_END = time(8, 30)
# Afternoons before a school day show tomorrow's view in this window.
AFTERNOON_START, AFTERNOON_END = time(15, 0), time(19, 0)

# SkoleIntra's lesson codes, as a child reads them. Soft hyphens (U+00AD) let a
# long name break inside a narrow tile; codes not listed are shown as given.
SUBJECTS = {
    "DAN": "Dansk", "MAT": "Mate­matik", "ENG": "Engelsk", "TYS": "Tysk",
    "IDR": "Idræt", "BIL": "Billed­kunst", "MUS": "Musik",
    "N/T": "Natur/­teknologi", "HIS": "Historie", "KRI": "Kristen­dom",
    "HDS": "Håndværk og design", "H/D": "Håndværk og design",
    "MAD": "Mad­kundskab", "GEO": "Geografi", "BIO": "Biologi",
    "FYS": "Fysik/­kemi", "SAM": "Samfunds­fag", "KULTUR": "Kultur",
    "STØTTE": "Støtte­time", "LEG": "Leg",
}
# Short forms for a tile too narrow for the full name.
SHORT_SUBJECTS = {
    "Mate­matik": "Mat.", "Billed­kunst": "Billed.", "Natur/­teknologi": "N/T",
    "Kristen­dom": "Kristend.", "Håndværk og design": "H&D",
    "Mad­kundskab": "Madk.", "Samfunds­fag": "Samf.", "Støtte­time": "Støtte",
    "Fysik/­kemi": "Fys/kemi", "Engelsk": "Eng.",
}
# Support lessons that run beside the class's own lesson; the class lesson is shown.
SUPPORT_CODES = frozenset({"STØTTE", "SPE"})
# Lessons of one subject separated by at most a short break read as one block.
MERGE_GAP_MINUTES = 15
CANCEL_FIELDS = ("cancelled", "canceled", "isCancelled", "isCanceled")
_CLOCK = re.compile(r"^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})")


def view_date(at: datetime) -> date | None:
    """The school date the children's view shows at `at`, or None for the parents' board."""
    if at.tzinfo is None:
        raise ValueError("dashboard timing requires a timezone")
    local = at.astimezone(ZONE)
    focus = dashboard_focus(local)
    if focus.mode != "schoolday":
        return None
    clock = local.timetz().replace(tzinfo=None)
    if clock < MORNING_END and focus.school_date == local.date():
        return focus.school_date
    if (AFTERNOON_START <= clock < AFTERNOON_END
            and focus.school_date == local.date() + timedelta(days=1)):
        return focus.school_date
    return None


def change_times(day: date) -> list[time]:
    """Clock times on `day` at which the children's view starts or ends."""
    times = []
    if day.weekday() < 5:
        times.append(MORNING_END)
    if day.weekday() in (6, 0, 1, 2, 3):
        times += [AFTERNOON_START, AFTERNOON_END]
    return times


def subject_name(code: str) -> str:
    code = " ".join(str(code or "").split())
    return SUBJECTS.get(code.upper(), code) if code else "Lektion"


def _clock(value: Any) -> str | None:
    match = _CLOCK.match(str(value or ""))
    return f"{match.group(1)}:{match.group(2)}" if match else None


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def lesson_change(event: dict[str, Any]) -> tuple[str | None, str]:
    """('aflyst', ''), ('vikar', first name) or (None, '') for one lesson slot.

    SkoleIntra sometimes repeats the scheduled teacher as `substituteStaff`; that
    is the ordinary teacher, not a change (the same rule as the derivation).
    """
    title = str(event.get("title") or "")
    if any(event.get(field) for field in CANCEL_FIELDS) or DANISH.lesson_is_cancelled(title):
        return "aflyst", ""
    substitute = " ".join(str(event.get("substituteStaff") or "").strip(" ()").split())
    scheduled = " ".join(str(event.get("staffName") or "").strip(" ()").split())
    if substitute and substitute.casefold() != scheduled.casefold():
        return "vikar", substitute.split()[0][:24]
    return None, ""


def day_lessons(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One child's lesson slots for a day as display blocks, earliest first.

    Parallel slots become one: a support lesson gives way to the class lesson,
    and a change wins over the unchanged copy. Consecutive lessons of one subject
    with the same change merge into one block across a short break.
    """
    slots: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        start, end = _clock(event.get("start") or event.get("startDate")), _clock(
            event.get("end") or event.get("endDate"))
        if not start or not end or _minutes(end) <= _minutes(start) or event.get("allDay"):
            continue
        code = " ".join(str(event.get("lessonName") or event.get("title") or "").split())
        change, note = lesson_change(event)
        slots.setdefault(start, []).append(
            {"start": start, "end": end, "code": code, "change": change, "note": note})
    blocks: list[dict[str, Any]] = []
    for start in sorted(slots, key=_minutes):
        group = slots[start]
        main = [item for item in group if item["code"].upper() not in SUPPORT_CODES] or group
        pick = next((item for item in main if item["change"]), main[0])
        lesson = {"start": pick["start"], "end": max((item["end"] for item in main), key=_minutes),
                  "subject": subject_name(pick["code"]), "change": pick["change"], "note": pick["note"]}
        previous = blocks[-1] if blocks else None
        if (previous and all(previous[key] == lesson[key] for key in ("subject", "change", "note"))
                and 0 <= _minutes(lesson["start"]) - _minutes(previous["end"]) <= MERGE_GAP_MINUTES):
            previous["end"] = lesson["end"]
        else:
            blocks.append(lesson)
    return blocks


def build_day_view(day: date, names: list[str], label: str,
                   events_by_child: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """The `day_view` state field, or None when neither child has a lesson that day."""
    children = [{"name": name, "lessons": day_lessons(events_by_child.get(name, []))[:10]}
                for name in names]
    if not any(child["lessons"] for child in children):
        return None
    starts = {lesson["start"] for child in children for lesson in child["lessons"]}
    # The bell schedule: every slot start of either child, so their rows line up.
    for events in events_by_child.values():
        for event in events:
            clock = _clock(event.get("start") or event.get("startDate"))
            if clock and any(lesson["start"] <= clock < lesson["end"]
                             for child in children for lesson in child["lessons"]):
                starts.add(clock)
    slots = sorted(starts, key=_minutes)[:10]
    for child in children:
        child["lessons"] = [lesson for lesson in child["lessons"] if lesson["start"] in slots]
    return {"date": day.isoformat(), "label": label, "slots": slots, "children": children}


def lesson_lookup(store) -> Callable[[date], dict[str, list[dict[str, Any]]]]:
    """`lookup(day)` → each child's raw lesson slots on that day, read once per day."""
    days: dict[date, dict[str, list[dict[str, Any]]]] = {}

    def lookup(day: date) -> dict[str, list[dict[str, Any]]]:
        if day not in days:
            result: dict[str, list[dict[str, Any]]] = {}
            for child, event in store.lesson_slots(day.isoformat()):
                result.setdefault(child, []).append(event)
            days[day] = result
        return days[day]
    return lookup


def attach_day_view(state: dict[str, Any], lookup) -> dict[str, Any]:
    """A copy of `state` with the children's view when its moment calls for one.

    The moment is the frame's `composed_at`. The view needs the hero to show the
    same school day, so the remember list is that day's own.
    """
    if "weather" in state:
        return state
    composed = state.get("freshness", {}).get("composed_at")
    if not composed:
        return state
    at = datetime.fromisoformat(composed)
    day = view_date(at)
    if day is None:
        return state
    if state["hero"]["date"] != day.isoformat():
        return state
    names = [child["name"] for child in state["hero"]["children"]]
    label = "I dag" if day == at.astimezone(ZONE).date() else "I morgen"
    view = build_day_view(day, names, label, lookup(day))
    return {**state, "day_view": view} if view else state


def clothing(item: dict[str, Any] | None) -> str:
    """What to wear for the school day's weather, in a child's words; '' without weather."""
    if not item:
        return ""
    low, high, rain = item["low"], item["high"], item.get("rain_mm", 0)
    if item["icon"] == "snow" or low <= 0:
        wear = ["vinterjakke", "hue", "vanter"]
    elif low <= 5:
        wear = ["jakke", "hue"]
    elif high <= 14:
        wear = ["jakke"]
    elif high <= 19:
        wear = ["trøje"]
    else:
        wear = ["t-shirt"]
    if rain >= 1 or item["icon"] in ("rain", "storm"):
        wear.append("regntøj")
    text = wear[0] if len(wear) == 1 else ", ".join(wear[:-1]) + " og " + wear[-1]
    return text[:1].upper() + text[1:]
