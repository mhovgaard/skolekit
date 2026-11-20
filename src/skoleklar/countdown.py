"""The parents' board's countdown: the next family date within reach, one line.

Built-in dates (fastelavn, påske, the holidays with no school, sankthans,
halloween, mortensaften, juleaften, nytårsaften, solhverv and jævndøgn) and the
household's own birthdays and dates from `[countdown]` in skoleklar.toml:

```toml
[countdown]
builtin = true             # optional; false leaves only the household's dates

[[countdown.birthdays]]
name = "Alfa"              # as the board says it: "30 dage til Alfa", "Tillykke, Alfa!"
date = "03-14"             # month-day: the board never needs a year or an age
window = 30                # optional; days ahead it shows (default 30)

[[countdown.dates]]        # ferier and one-off dates
name = "juleferie"
date = "2026-12-19"        # with a year: that year only; "12-19" is every year
window = 21                # optional (default 14)
```

Each date shows from `window` days ahead; the nearest open date wins, a birthday
before a household date before a built-in one on the same day. Only the one line
reaches the board state (`countdown`); the dates themselves never do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from skolekit.profile import ProfileError
from skoleklar.meaning import MEANING_FILE

BIRTHDAY_WINDOW, DATE_WINDOW, MAX_WINDOW = 30, 14, 60
_DAY = re.compile(r"(?:(?P<year>\d{4})-)?(?P<month>\d{2})-(?P<day>\d{2})")
WHERE = f"{MEANING_FILE} [countdown]"


@dataclass(frozen=True)
class Mark:
    """One date to count down to; `kind` orders a tie: birthday, then own date, then built-in."""
    name: str
    month: int
    day: int
    window: int
    kind: str = "builtin"  # "birthday", "date" or "builtin"
    year: int | None = None


@dataclass(frozen=True)
class Countdown:
    builtin: bool = True
    marks: tuple[Mark, ...] = ()


# --- Built-in dates --------------------------------------------------------------------

def easter(year: int) -> date:
    """Easter Sunday in the Gregorian calendar (the anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    h = (19 * a + b - b // 4 - (8 * b + 13) // 25 + 15) % 30
    l = (32 + 2 * (b % 4) + 2 * (c // 4) - h - c % 4) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


# Mean solstices and equinoxes (Meeus, Astronomical Algorithms, table 27.B, years
# 2000–3000) as Julian ephemeris days; a few minutes off, which only matters when
# the moment falls just around midnight.
_SEASONS = {
    3: (2451623.80984, 365242.37404, 0.05169, -0.00411, -0.00057),
    6: (2451716.56767, 365241.62603, 0.00325, 0.00888, -0.00030),
    9: (2451810.21715, 365242.01767, -0.11575, 0.00337, 0.00078),
    12: (2451900.05952, 365242.74049, -0.06223, -0.00823, 0.00032),
}


def season_start(year: int, month: int, zone) -> date:
    """The local date of the March or September equinox, or the June or December solstice."""
    y = (year - 2000) / 1000
    jde = sum(term * y ** power for power, term in enumerate(_SEASONS[month]))
    moment = datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=jde - 2451545.0)
    return moment.astimezone(zone).date()


# (name, window) of the built-in dates; a window of 0 shows on the day only.
WINDOWS = {"fastelavn": 14, "påske": 14, "Kr. himmelfart": 7, "2. pinsedag": 7, "sankthans": 14,
           "halloween": 14, "mortensaften": 7, "juleaften": 45, "nytårsaften": 6,
           "vintersolhverv": 0, "sommersolhverv": 0, "jævndøgn": 0}
# A school holiday: said as a day off.
DAYS_OFF = {"Kr. himmelfart", "2. pinsedag"}


def builtin_dates(year: int, zone) -> list[tuple[date, str]]:
    sunday = easter(year)
    return [(sunday - timedelta(days=49), "fastelavn"), (sunday, "påske"),
            (sunday + timedelta(days=39), "Kr. himmelfart"), (sunday + timedelta(days=50), "2. pinsedag"),
            (date(year, 6, 23), "sankthans"), (date(year, 10, 31), "halloween"),
            (date(year, 11, 10), "mortensaften"), (date(year, 12, 24), "juleaften"),
            (date(year, 12, 31), "nytårsaften"),
            (season_start(year, 3, zone), "jævndøgn"), (season_start(year, 6, zone), "sommersolhverv"),
            (season_start(year, 9, zone), "jævndøgn"), (season_start(year, 12, zone), "vintersolhverv")]


# --- Wording ---------------------------------------------------------------------------

# On the day, a solstice or an equinox says what it means.
_SEASON_LINES = {"vintersolhverv": ("Solhverv:", "lyset vender"), "sommersolhverv": ("Solhverv:", "længste dag"),
                 "jævndøgn": ("Jævndøgn:", "dag = nat")}


def wording(name: str, days: int, kind: str) -> tuple[str, str]:
    """(bold lead, rest) of the line: `45` `dage til juleaften`, `I morgen:` `mortensaften`."""
    if kind == "builtin" and name in _SEASON_LINES:
        return _SEASON_LINES[name]
    if days == 0:
        if kind == "birthday":
            return "Tillykke,", f"{name}!"
        return ("Fri i dag:" if name in DAYS_OFF else "I dag:"), name
    if days == 1:
        return ("Fri i morgen:" if name in DAYS_OFF else "I morgen:"), name
    return str(days), f"dage til {name}"


def line(config: Countdown, today: date, zone) -> dict[str, Any] | None:
    """The board's countdown on `today`: {"lead", "text", "flag"}, or None when nothing is near."""
    order = {"birthday": 0, "date": 1, "builtin": 2}
    found = []
    for year in (today.year, today.year + 1):
        candidates = [(when, Mark(name, when.month, when.day, WINDOWS[name]))
                      for when, name in builtin_dates(year, zone)] if config.builtin else []
        for mark in config.marks:
            if mark.year in (None, year):
                candidates.append((_on(year, mark.month, mark.day), mark))
        for when, mark in candidates:
            days = (when - today).days
            if 0 <= days <= mark.window:
                found.append((days, order[mark.kind], len(found), mark))
    if not found:
        return None
    days, _, _, mark = min(found)
    lead, text = wording(mark.name, days, mark.kind)
    return {"lead": lead, "text": text, "flag": mark.kind == "birthday"}


def _on(year: int, month: int, day: int) -> date:
    # A 29 February birthday is on the 28th in other years.
    if (month, day) == (2, 29) and not (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        return date(year, 2, 28)
    return date(year, month, day)


# --- The household's file --------------------------------------------------------------

def _mark(row: Any, kind: str, number: int) -> Mark:
    table = "birthdays" if kind == "birthday" else "dates"
    where = f"{MEANING_FILE} [[countdown.{table}]] {number}"
    if not isinstance(row, dict):
        raise ProfileError(f"{where}: must be a table")
    unknown = set(row) - {"name", "date", "window"}
    if unknown:
        raise ProfileError(f"{where}: unknown key {min(unknown)!r}")
    name, value = row.get("name"), row.get("date")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24:
        raise ProfileError(f"{where}: name is 1-24 characters")
    name = name.strip()
    match = _DAY.fullmatch(str(value)) if isinstance(value, str) else None
    if not match or (kind == "birthday" and match["year"]):
        shape = "'MM-DD'" if kind == "birthday" else "'YYYY-MM-DD' or 'MM-DD'"
        raise ProfileError(f"{where} {name!r}: date is {shape}")
    year = int(match["year"]) if match["year"] else None
    try:
        date(year or 2000, int(match["month"]), int(match["day"]))
    except ValueError:
        raise ProfileError(f"{where} {name!r}: {value} is not a date") from None
    window = row.get("window", BIRTHDAY_WINDOW if kind == "birthday" else DATE_WINDOW)
    if not isinstance(window, int) or isinstance(window, bool) or not 0 <= window <= MAX_WINDOW:
        raise ProfileError(f"{where} {name!r}: window is a whole number of days from 0 to {MAX_WINDOW}")
    mark = Mark(name, int(match["month"]), int(match["day"]), window, kind, year)
    _check_fits(mark, where)
    return mark


def _check_fits(mark: Mark, where: str) -> None:
    """Refuse a name the panel's header cannot show in every wording it may get."""
    try:
        from skoleklar.parents_board_render import countdown_room, countdown_width
    except ImportError:  # no drawing libraries here; the board hides what does not fit
        return
    # Only the wordings its window can reach: the day itself, the day before, a count.
    for days in [0] + [1] * (mark.window >= 1) + [mark.window] * (mark.window >= 2):
        lead, text = wording(mark.name, days, mark.kind)
        if countdown_width(lead, text, mark.kind == "birthday") > countdown_room():
            raise ProfileError(f"{where} {mark.name!r}: too long for the board; use a shorter name")


def parse_countdown(document: dict[str, Any] | None) -> Countdown:
    table = (document or {}).get("countdown", {})
    if not isinstance(table, dict):
        raise ProfileError(f"{WHERE} must be a table")
    unknown = set(table) - {"builtin", "birthdays", "dates"}
    if unknown:
        raise ProfileError(f"{WHERE} unknown key {min(unknown)!r}")
    builtin = table.get("builtin", True)
    if not isinstance(builtin, bool):
        raise ProfileError(f"{WHERE} builtin is true or false")
    marks = []
    for key, kind in (("birthdays", "birthday"), ("dates", "date")):
        rows = table.get(key, [])
        if not isinstance(rows, list):
            raise ProfileError(f"{WHERE} {key} is a list of [[countdown.{key}]] tables")
        marks += [_mark(row, kind, number) for number, row in enumerate(rows, start=1)]
    return Countdown(builtin, tuple(marks))


def load_countdown(home) -> Countdown:
    """`[countdown]` from `home`/skoleklar.toml; the built-in dates without the file."""
    from skoleklar.meaning import read_household
    return parse_countdown(read_household(home))
