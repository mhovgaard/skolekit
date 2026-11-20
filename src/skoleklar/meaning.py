"""What a source's content means for the household: `[meaning]`.

Packaged `meaning.toml` supplies booking defaults; private `skoleklar.toml`
overrides the household's meaning rules. The toolkit only parses school
wording and never reads these product rules.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from skolekit.profile import ProfileError
from skolekit.school import School

MEANING_FILE = "skoleklar.toml"
# bookings.kind's CHECK in migrations/0003_bookings.sql.
BOOKING_KINDS = ("conference", "health", "event", "sfo")
ROUNDS = ("signup_until",)
SCHOOL_WIDE = ("every_class_code",)


@dataclass(frozen=True, slots=True)
class Meaning:
    booking_form_types: tuple[str, ...]
    booking_kinds: tuple[tuple[str, str], ...]
    default_kind: str
    default_minutes: int
    round: str
    school_wide: str

    def booking_kind(self, title: str) -> str:
        for kind, pattern in self.booking_kinds:
            if re.search(pattern, title or "", re.I):
                return kind
        return self.default_kind

    def school_wide_levels(self, school: School) -> int:
        """How many class levels an event lists when it is for the whole school."""
        return len(school.class_codes)


def _defaults() -> dict[str, Any]:
    text = files("skoleklar").joinpath("meaning.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)["meaning"]


def _merge(base: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    # [runtime], [weather] and [gcp] say where and how the household runs (skoleklar.runtime.config);
    # [countdown] is the parents' board's dates (skoleklar.countdown).
    unknown = set(document) - {"meaning", "runtime", "weather", "gcp", "countdown"}
    if unknown:
        raise ProfileError(f"{MEANING_FILE}: unknown table [{sorted(unknown)[0]}]")
    given = document.get("meaning", {})
    if not isinstance(given, dict):
        raise ProfileError(f"{MEANING_FILE}: [meaning] must be a table")
    merged = {table: dict(values) for table, values in base.items()}
    for table, values in given.items():
        if table not in merged or not isinstance(values, dict):
            raise ProfileError(f"{MEANING_FILE}: unknown table [meaning.{table}]")
        for key, value in values.items():
            if key not in merged[table]:
                raise ProfileError(f"{MEANING_FILE}: [meaning.{table}] unknown key {key!r}")
            merged[table][key] = value
    return merged


def parse_meaning(document: dict[str, Any] | None = None) -> Meaning:
    """Today's rules, with the household's file over them."""
    values = _merge(_defaults(), document or {})
    bookings, calendar = values["bookings"], values["calendar"]
    where = f"{MEANING_FILE} [meaning.bookings]"
    types = bookings["form_types"]
    if not isinstance(types, list) or not types or not all(isinstance(item, str) and item for item in types):
        raise ProfileError(f"{where} form_types must be a list of form types")
    kinds = bookings["kinds"]
    if not isinstance(kinds, dict) or not all(isinstance(value, str) and value for value in kinds.values()):
        raise ProfileError(f"{where} kinds must map a kind to a title pattern")
    for kind, pattern in kinds.items():
        if kind not in BOOKING_KINDS:
            raise ProfileError(f"{where} kinds: {kind!r} is not one of {', '.join(BOOKING_KINDS)}")
        try:
            re.compile(pattern)
        except re.error as error:
            raise ProfileError(f"{where} kinds {kind}: {error}") from None
    if bookings["default_kind"] not in BOOKING_KINDS:
        raise ProfileError(f"{where} default_kind must be one of {', '.join(BOOKING_KINDS)}")
    minutes = bookings["default_minutes"]
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 5 <= minutes <= 240:
        raise ProfileError(f"{where} default_minutes must be a whole number from 5 to 240")
    if bookings["round"] not in ROUNDS:
        raise ProfileError(f"{where} round: only {', '.join(ROUNDS)} is read so far")
    if calendar["school_wide"] not in SCHOOL_WIDE:
        raise ProfileError(f"{MEANING_FILE} [meaning.calendar] school_wide: only "
                           f"{', '.join(SCHOOL_WIDE)} is read so far")
    return Meaning(
        booking_form_types=tuple(types),
        booking_kinds=tuple(kinds.items()),
        default_kind=bookings["default_kind"],
        default_minutes=minutes,
        round=bookings["round"],
        school_wide=calendar["school_wide"],
    )


DEFAULT_MEANING = parse_meaning()


def read_household(home: Path) -> dict[str, Any] | None:
    """`home`/skoleklar.toml as a document, or None without the file.

    A scan reads it once and hands the same document to each table's parser
    (`parse_meaning`, `parse_runtime`, `parse_countdown`).
    """
    path = home / MEANING_FILE
    if not path.exists():
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"{MEANING_FILE}: {error}") from None


def load_meaning(home: Path) -> Meaning:
    """The household's meaning from `home`/skoleklar.toml, or today's rules without it."""
    document = read_household(home)
    return DEFAULT_MEANING if document is None else parse_meaning(document)
