"""What a school's pages say: the locale's dates and the school's wording, as rules.

The locale (`locales/da.toml`) supplies dates and known wording. A school
overrides only differing rules in its private configuration. Parsers use
`Wording` rather than hardcoding a school's text.
"""
from __future__ import annotations

import functools
import re
import tomllib
from dataclasses import dataclass, replace
from datetime import date
from importlib.resources import files
from typing import Any

# What each placeholder captures. {date} and {time} come from the locale.
_TEXT = {
    "from": r"(?P<from_text>.+?)",
    "until": r"(?P<until_text>.+?)",
    "expires": r"(?P<expires_text>.+?)",
    "dates": r"(?P<dates_text>.+?)",
    "minutes": r"(?P<minutes>\d{1,3})",
}
_PLACEHOLDER = re.compile(r"\{(date|time|from|until|expires|dates|minutes)\}")
# Each rule, the table and key it lives under, and the placeholders it must hold once each.
RULES = {
    "booked": ("forms", ("date", "time")),
    "answered": ("forms", ("date",)),
    "signup": ("notices", ("from", "until")),
    "expires": ("notices", ("expires",)),
    "days": ("notices", ("dates",)),
    "minutes": ("notices", ("minutes",)),
}
# What a weekly plan's section can hold; the day summaries read these names.
SECTION_KINDS = ("homework", "materials", "bring", "activity", "program")
# A notice's stated length outside these bounds is a misreading, not a booking length.
MINUTES_RANGE = (5, 240)


class WordingError(ValueError):
    """A rule that can't be used as written."""


@functools.lru_cache(maxsize=None)
def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, flags)


@dataclass(frozen=True, slots=True)
class Wording:
    """The locale's dates and one school's words, as rules over them."""

    locale: str
    months: tuple[str, ...]
    date: str
    time: str
    yesterday: str
    today: str
    broad: tuple[str, ...]
    cancelled_lessons: tuple[str, ...]
    booked: str
    answered: str
    signup: str
    expires: str
    days: str
    minutes: str
    sections: tuple[tuple[str, tuple[str, ...]], ...]

    # --- The rules, compiled --------------------------------------------------------------

    @property
    def date_pattern(self) -> str:
        return self.date.replace("{months}", "|".join(re.escape(month) for month in self.months))

    def rule(self, name: str) -> re.Pattern[str]:
        """The named rule with the locale's placeholders filled in."""
        fill = {"date": self.date_pattern, "time": self.time, **_TEXT}
        return _compile(_PLACEHOLDER.sub(lambda match: fill[match.group(1)], getattr(self, name)))

    def section_pattern(self) -> re.Pattern[str]:
        headings = "|".join(pattern for _, patterns in self.sections for pattern in patterns)
        return _compile(rf"^(?P<heading>{headings})\s*:\s*(?P<remainder>.*)$", re.I)

    # --- Dates ----------------------------------------------------------------------------

    def month(self, name: str) -> int | None:
        """1 for 'jan', 'januar' or 'Jan.'; None for a word that isn't a month."""
        prefix = name.lower()[:3]
        return self.months.index(prefix) + 1 if prefix in self.months else None

    def _day(self, match: re.Match[str]) -> str | None:
        month = self.month(match.group("month"))
        try:
            return date(int(match.group("year")), month, int(match.group("day"))).isoformat() if month else None
        except ValueError:
            return None

    def read_date(self, text: str | None) -> str | None:
        """`2026-11-05` for the first `5. nov. 2026` in `text`, or None."""
        match = _compile(self.date_pattern, re.I).search(text or "")
        return self._day(match) if match else None

    def read_dates(self, text: str | None) -> list[str]:
        days = (self._day(match) for match in _compile(self.date_pattern, re.I).finditer(text or ""))
        return list(dict.fromkeys(day for day in days if day))

    # --- What a form's status and a notice's text hold -------------------------------------

    def booked_time(self, status: str | None) -> str | None:
        """`2026-11-09T15:00` for a status that books 9 November at 15:00, or None."""
        match = self.rule("booked").search(status or "")
        if not match:
            return None
        day = self._day(match)
        hour, minute = int(match.group("hour")), int(match.group("minute"))
        if day is None or hour > 23 or minute > 59:
            return None
        return f"{day}T{hour:02d}:{minute:02d}"

    def answered_on(self, status: str | None) -> str | None:
        match = self.rule("answered").search(status or "")
        return self._day(match) if match else None

    def signup_window(self, text: str | None) -> tuple[str | None, str | None]:
        match = self.rule("signup").search(text or "")
        if not match:
            return None, None
        return self.read_date(match.group("from_text")), self.read_date(match.group("until_text"))

    def expires_on(self, text: str | None) -> str | None:
        match = self.rule("expires").search(text or "")
        return self.read_date(match.group("expires_text")) if match else None

    def notice_days(self, text: str | None) -> list[str]:
        match = self.rule("days").search(text or "")
        return self.read_dates(match.group("dates_text")) if match else []

    def notice_minutes(self, text: str | None) -> int | None:
        match = self.rule("minutes").search(text or "")
        if not match:
            return None
        minutes = int(match.group("minutes"))
        return minutes if MINUTES_RANGE[0] <= minutes <= MINUTES_RANGE[1] else None

    # --- Weekly plans and classes ---------------------------------------------------------

    def section_kind(self, heading: str) -> str:
        """What a weekly plan's section holds, by its heading; 'unsectioned' for none."""
        for kind, patterns in self.sections:
            if any(_compile(pattern, re.I).fullmatch(heading.strip()) for pattern in patterns):
                return kind
        return "unsectioned"

    def lesson_is_cancelled(self, title: str) -> bool:
        """True when a lesson's title holds one of the school's words for cancelled (`[lessons]`)."""
        folded = (title or "").casefold()
        return any(word.casefold() in folded for word in self.cancelled_lessons)

    def names_several_classes(self, text: str) -> bool:
        if not self.broad:
            return False
        words = "|".join(re.escape(word) for word in self.broad)
        return bool(_compile(rf"\b({words})\b", re.I).search(text))


# --- Reading the locale and a school's overrides ---------------------------------------------

def _strings(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise WordingError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def _check_rule(wording: Wording, name: str, where: str) -> None:
    pattern = getattr(wording, name)
    if not isinstance(pattern, str) or not pattern:
        raise WordingError(f"{where} must be a non-empty string")
    used = _PLACEHOLDER.findall(pattern)
    needed = RULES[name][1]
    if sorted(used) != sorted(needed):
        wanted = ", ".join("{" + placeholder + "}" for placeholder in needed)
        raise WordingError(f"{where} must hold {wanted}, once each")
    try:
        wording.rule(name)
    except re.error as error:
        raise WordingError(f"{where} is not a regular expression: {error}") from None


def _check_sections(sections: tuple[tuple[str, tuple[str, ...]], ...], where: str) -> None:
    for kind, patterns in sections:
        for pattern in patterns:
            try:
                compiled = re.compile(pattern, re.I)
            except re.error as error:
                raise WordingError(f"{where} {kind}: not a regular expression: {error}") from None
            if compiled.groups:
                raise WordingError(f"{where} {kind}: use (?:...) for groups, so the heading stays whole")


def with_overrides(base: Wording, document: dict[str, Any]) -> Wording:
    """`base` with the rules a school's file sets: [classes] broad, [lessons], [forms], [notices], [weekly_plan]."""
    values: dict[str, Any] = {}
    classes = document.get("classes", {})
    if "broad" in classes:
        values["broad"] = _strings(classes["broad"], "[classes] broad")
    lessons = document.get("lessons", {})
    if not isinstance(lessons, dict) or set(lessons) - {"cancelled"}:
        raise WordingError("[lessons] holds only cancelled")
    if "cancelled" in lessons:
        values["cancelled_lessons"] = _strings(lessons["cancelled"], "[lessons] cancelled")
    for table in ("forms", "notices"):
        section = document.get(table, {})
        if not isinstance(section, dict):
            raise WordingError(f"[{table}] must be a table")
        for key, value in section.items():
            if key not in RULES or RULES[key][0] != table:
                raise WordingError(f"[{table}]: unknown rule {key!r}")
            values[key] = value
    plan = document.get("weekly_plan", {})
    if not isinstance(plan, dict) or set(plan) - {"sections"}:
        raise WordingError("[weekly_plan] holds only [weekly_plan.sections]")
    if "sections" in plan:
        given = plan["sections"]
        if not isinstance(given, dict):
            raise WordingError("[weekly_plan.sections] must be a table")
        unknown = set(given) - set(SECTION_KINDS)
        if unknown:
            raise WordingError(f"[weekly_plan.sections]: unknown kind {sorted(unknown)[0]!r}; "
                               f"the kinds are {', '.join(SECTION_KINDS)}")
        sections = tuple((kind, _strings(given[kind], f"[weekly_plan.sections] {kind}")
                          if kind in given else patterns) for kind, patterns in base.sections)
        _check_sections(sections, "[weekly_plan.sections]")
        values["sections"] = sections
    wording = replace(base, **values)
    for name in values.keys() & RULES.keys():
        _check_rule(wording, name, f"[{RULES[name][0]}] {name}")
    return wording


def load_locale(name: str) -> Wording:
    """The locale shipped in `skolekit/locales/{name}.toml`."""
    resource = files("skolekit").joinpath("locales", f"{name}.toml")
    if not resource.is_file():
        raise WordingError(f"no locale {name!r} ships with skolekit")
    document = tomllib.loads(resource.read_text(encoding="utf-8"))
    dates = document["dates"]
    plan = document["weekly_plan"]["sections"]
    wording = Wording(
        locale=name,
        months=_strings(dates["months"], "[dates] months"),
        date=dates["date"], time=dates["time"],
        yesterday=dates["yesterday"], today=dates["today"],
        broad=_strings(document["classes"]["broad"], "[classes] broad"),
        cancelled_lessons=_strings(document["lessons"]["cancelled"], "[lessons] cancelled"),
        **{key: document[table][key] for key, (table, _) in RULES.items()},
        sections=tuple((kind, _strings(plan[kind], kind)) for kind in SECTION_KINDS),
    )
    if len(wording.months) != 12:
        raise WordingError(f"locale {name!r}: list twelve months")
    for key, (table, _) in RULES.items():
        _check_rule(wording, key, f"locale {name!r} [{table}] {key}")
    _check_sections(wording.sections, f"locale {name!r} [weekly_plan.sections]")
    return wording


DANISH = load_locale("da")
