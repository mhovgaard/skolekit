"""Draw the parents' board on the panel (PNG) and in the browser (HTML).

One row per child across the panel, with the dividers aligned on both sides.
Left (dark): the next school day — the weekly plan's headline, homework with a
checkbox, what to bring and the child's latest message. Right: the four school
days after it as dated rows (a weekend day in between only when something is on),
each led by the weekly plan's headline for the day, then homework, events in a
blue category box and the rest; then events up to two weeks out below a dotted rule. A bottom `Begge` row carries
what concerns both children and the unread total; the top right is the update
stamp and the board's battery.

Yellow marks time urgency only: a deadline or a changed time within two days.
Nothing is cut mid-line. When a week does not fit, whole items give way in a
fixed order (`DROP_STEPS`) and the row says how many. See docs/parents-board.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from skoleklar import dashboard_renderer as board
from skoleklar import health
from skoleklar.parents_board import lookahead_span
from skoleklar.present import urgent_on

WIDTH, HEIGHT = board.WIDTH, board.HEIGHT
SPLIT = board.PANEL_SPLIT_X
# The frame's white mat is the margin, so the board runs almost to the edge.
LEFT_X, LEFT_END = 10, SPLIT - 14
RIGHT_X, RIGHT_END = SPLIT + 14, WIDTH - 10
DATE_WIDTH = 60
TEXT_X = RIGHT_X + DATE_WIDTH + 8
# The second homework item of a day starts here (from TEXT_X), so pairs form two columns.
TAB = 166
HEADER_HEIGHT = 43
DIVIDER = 2
SHORT_DAYS = ("Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn")
# (size, bold, serif) per style; 14 px is the panel's legibility floor.
STYLES = {
    "date": (26, True, True), "date_s": (23, True, True), "name": (23, True, True), "begge": (19, True, True),
    "wx": (15, True, False), "status": (14, False, False), "status_b": (14, True, False),
    "plan": (16, False, False), "plan_b": (16, True, False),
    "hw": (18, False, False), "hw_b": (18, True, False),
    "item": (16, False, False), "item_b": (16, True, False),
    "r": (15, False, False), "r_b": (15, True, False), "r_plan": (16, False, True),
    "chip": (14, True, False), "msg": (15, False, False), "msg_b": (15, True, False),
}
LINE_HEIGHT = {"plan": 21, "hw": 23, "item": 21, "r": 20, "msg": 20}
LEFT_PAD = (7, 9)
RIGHT_PAD = (8, 9)
BEGGE_PAD = (6, 8)
NAME_HEIGHT = 25
@dataclass
class Item:
    """One thing on the board: a homework item, a bring line, an event …"""
    kind: str
    runs: list[tuple[str, bool]]
    chip: str = ""
    box: bool = False
    urgent: bool = False


@dataclass
class Row:
    """One line of a column: up to two items, with an optional day label in the date column."""
    items: list[Item]
    label: str = ""
    note: str = ""
    rule: bool = False
    gap: bool = False
    indent: int = 0
    tab: int = TAB


@dataclass
class Message:
    sender: str
    topic: str
    when: str
    unread: bool


@dataclass
class Kid:
    name: str
    plan: str = ""
    left: list[Item] = field(default_factory=list)
    message: Message | None = None
    days: list[tuple[str, list[Item], bool]] = field(default_factory=list)  # (iso date, items, rain)
    later: list[tuple[str, Item]] = field(default_factory=list)
    dropped_left: int = 0
    dropped_right: int = 0
    later_folded: int = 0


@dataclass
class Model:
    day_title: str
    weather: tuple[str, str, str] | None  # (icon, temperatures, advice)
    status: str
    stale: bool
    battery: int | None
    kids: list[Kid]
    shared_days: list[tuple[str, list[Item], bool]]
    shared_later: list[tuple[str, Item]]
    shared_folded: int
    unread: str
    tight: bool = False


# --- Fonts and measuring -------------------------------------------------------------

@lru_cache(maxsize=None)
def face(style: str):
    """The font for a style and the y shift that puts a serif on the sans baseline."""
    size, bold, serif = STYLES[style]
    selected = board.font(size, bold, serif)
    shift = board.font(size, bold).getmetrics()[0] - selected.getmetrics()[0] if serif else 0
    return selected, shift


@lru_cache(maxsize=1)
def _ruler():
    from PIL import Image, ImageDraw
    return ImageDraw.Draw(Image.new("L", (1, 1)))


def text_width(value: str, style: str) -> float:
    return _ruler().textlength(value, font=face(style)[0])


def _styles(family: str, item: Item | None = None) -> tuple[str, str]:
    """(regular, bold) styles; a plan line on the right is set in the serif."""
    if item is not None and item.kind == "plan" and family == "r":
        return "r_plan", "r_plan"
    return family, f"{family}_b"


# --- Content ---------------------------------------------------------------------------

def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def make_item(entry: dict[str, Any], today: date | None) -> Item:
    """The board item for one line of the state: what `present` returned, drawn as it is.

    A homework line is its checkbox, its bold subject and its text; another line
    is its box, its bold label and its text. Yellow is the line's urgency window
    on `today`.
    """
    runs: list[tuple[str, bool]] = []
    for word in (entry["label"], entry["subject"]):
        if word:
            runs.append(((" " if runs else "") + word, True))
    text = entry["text"]
    runs.append(((" " + text) if runs else _capital(text), False))
    return Item(entry["kind"], runs, chip=entry["chip"], box=entry["box"], urgent=urgent_on(entry, today))


def _day_label(iso: str) -> str:
    value = date.fromisoformat(iso)
    return f"{SHORT_DAYS[value.weekday()]} {value.day}."


def plan_headline(data: dict[str, Any], name: str, iso: str | None) -> str:
    """The weekly plan's headline for a child's day, or '' when none is written."""
    for entry in data.get("day_summaries", []):
        if entry["date"] == iso:
            for summary in entry["children"]:
                if summary["name"] == name and summary["status"] == "ok":
                    return summary["headline"]
    return ""


def _words(text: str) -> set[str]:
    return {word.casefold() for word in re.findall(r"\w{3,}", text)}


def plan_item(headline: str, items: list[Item]) -> Item | None:
    """The day's plan headline as the first line of a day ahead, unless a line already says it.

    'Hundrede dages fest' adds nothing beside `FEST Hundrede dages fest`.
    """
    words = _words(headline)
    if not words or any(words <= _words("".join(text for text, _ in item.runs)) for item in items):
        return None
    return Item("plan", [(headline, False)])


def _when(activity: str) -> str:
    """'08/11' from '08/11 17:20'; any other date label is kept whole."""
    activity = str(activity or "").strip()
    return activity[:5] if re.match(r"\d{2}/\d{2}\b", activity) else activity


def _message(data: dict[str, Any], name: str, live_messages) -> Message | None:
    if live_messages is not None:
        item = next((entry for entry in live_messages if entry.child == name), None)
        if item is not None and item.status in {"safe", "redacted"} and item.sender \
                and item.sender != "Afsender ukendt":
            topic = getattr(item, "topic", "") if item.status == "safe" else ""
            return Message(item.sender.split()[0], topic, _when(item.activity), bool(item.unread))
    messages = data.get("messages", {})
    if messages.get("checked") and not board.is_stale(data):
        row = next((entry for entry in messages.get("items", []) if name in entry.get("children", [])), None)
        if row is not None:
            return Message(row["participant"].split()[0], "", _when(row.get("activity", "")),
                           bool(row.get("unread")))
    return None


def _unread(data: dict[str, Any], live_messages) -> str:
    messages = data.get("messages", {})
    if not messages.get("checked") or board.is_stale(data):
        return "Beskeder ikke tjekket"
    count = int(messages.get("unread_count", 0))
    return f"{count} ulæste i alt" if count else "Ingen ulæste"


def _status(data: dict[str, Any]) -> str:
    """'ÆLDRE DATA · Mangler: vejret · 2 punkter at tjekke · Opdateret 08/11 19.30'."""
    prefix = "ÆLDRE DATA · " if board.is_stale(data) else ""
    for part in (health.line(data.get("health")), board.review_label(data)):
        if part:
            prefix += part + " · "
    value = board.timestamp(data["freshness"]["source_updated_at"]).astimezone(board.ZONE)
    return prefix + f"Opdateret {value:%d/%m %H.%M}"


def _status_bold(data: dict[str, Any]) -> bool:
    """Old data or a missing part is said in bold, so it is noticed."""
    return board.is_stale(data) or bool(health.missing(data.get("health")))


def _weather(data: dict[str, Any], hero_date: str | None) -> tuple[str, str, str] | None:
    item = board.day_weather(data).get(hero_date) if hero_date else None
    if not item:
        return None
    if item["icon"] in {"rain", "storm"} or item["rain_mm"] >= 1:
        advice = "Regntøj"
    elif item["icon"] == "snow" or item["low"] <= 2:
        advice = "Vintertøj"
    else:
        advice = ""
    return item["icon"], board.temperature_range(item), advice


def _rain(data: dict[str, Any], iso: str, entries: list[dict[str, Any]]) -> bool:
    """Rain under the date on a trip or event day, unless its things to bring are rain gear."""
    weather = board.day_weather(data).get(iso)
    if not weather or not any(entry["chip"] for entry in entries):
        return False
    if weather["rain_mm"] < 1 and weather["icon"] not in {"rain", "storm"}:
        return False
    return not any(entry["rain_gear"] for entry in entries)


def _shown(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The lines the board draws: a weekly routine (morning singing) is left out."""
    return [entry for entry in entries if not entry["hidden"]]


def _order(items: list[Item]) -> list[Item]:
    """The plan line, homework, events, time-urgent lines, then the rest, keeping their order."""
    rank = {"plan": -1, "homework": 0, "trip": 1, "party": 1, "meeting": 1, "event": 1}
    return sorted(items, key=lambda item: rank.get(item.kind, 3 if not item.urgent else 2))


def _weekend_rows(data, names, kids, shared_days, later, window, today) -> None:
    """Place the composed weekend activities, in place.

    Inside the look-ahead they are day rows, replacing the look-ahead's own rows for
    that date; after it they are later events unless later events already name that
    date. Days before the next school day show only while the weekend cover should
    be up but its forecast is missing (Friday 15:00 to Sunday 12:00): a board for
    Monday never lists Sunday. An activity the weekend rows had no room for is
    counted on the last one, `Fødselsdagsfest (+1 mere)`, never on a line of its own.
    """
    if not today or not window:
        return
    from skoleklar.dashboard_timing import dashboard_focus

    composed = board.timestamp(data["freshness"]["composed_at"]).astimezone(board.ZONE)
    weekend_cover = dashboard_focus(composed).mode == "weekend"
    first = date.fromisoformat(window[0]) - timedelta(days=1)
    rows = []
    for row in data.get("weekend", []):
        day = date.fromisoformat(row["date"])
        if day < today or (day < first and not weekend_cover):
            continue
        # Today's activity is over by the afternoon.
        if day == today and composed.hour >= 15:
            continue
        entries = _shown(row["lines"])
        if entries:
            rows.append((day.isoformat(), row["who"], entries))
    if not rows:
        return
    last_window = window[-1]
    later_dates = {iso for entries in later.values() for iso, _ in entries}
    weekend_dates = {iso for iso, _, _ in rows}
    for kid in kids:
        kid.days = [day for day in kid.days if day[0] not in weekend_dates or day[0] > last_window]
    shared_days[:] = [day for day in shared_days if day[0] not in weekend_dates or day[0] > last_window]
    overflow = data.get("weekend_overflow", 0)
    for index, (iso, who, entries) in enumerate(rows):
        items = [make_item(entry, today) for entry in entries]
        if overflow and index == len(rows) - 1:
            items[-1].runs = [*items[-1].runs, (f" (+{overflow} mere)", False)]
        if iso > last_window:
            if iso not in later_dates:
                later.setdefault(who, []).append((iso, items[0]))
            continue
        target = shared_days if who == "Begge" else next(kid.days for kid in kids if kid.name == who)
        entry = next((day for day in target if day[0] == iso), None)
        if entry is None:
            target.append((iso, items, _rain(data, iso, entries)))
        else:
            entry[1].extend(items)
    for kid in kids:
        kid.days.sort(key=lambda day: day[0])
    shared_days.sort(key=lambda day: day[0])
    for entries in later.values():
        entries.sort(key=lambda entry: entry[0])


def build_model(data: dict[str, Any], live_messages=None) -> Model:
    hero = data["hero"]
    names = [child["name"] for child in hero["children"]]
    hero_date = hero["date"]
    composed = data["freshness"]["composed_at"]
    today = board.timestamp(composed).astimezone(board.ZONE).date()
    weekdays = {day["date"]: day for day in data.get("weekdays", [])}
    ahead = {row["date"]: row for row in data.get("ahead", [])}
    window = [day.isoformat() for day in lookahead_span(date.fromisoformat(hero_date))]
    kids = []
    for index, child in enumerate(hero["children"]):
        left = _order([make_item(entry, today) for entry in _shown(child["lines"])])
        kid = Kid(child["name"], plan_headline(data, child["name"], hero_date), left,
                  _message(data, child["name"], live_messages))
        for iso in window:
            entries = []
            if iso in weekdays:
                entries = _shown(weekdays[iso]["children"][index]["lines"])
            elif iso in ahead:
                entries = _shown(ahead[iso]["children"][index]["lines"])
            items = [item for item in _order([make_item(entry, today) for entry in entries])
                     if item.kind != "more" or len(entries) > 1]
            plan = plan_item(plan_headline(data, child["name"], iso), items)
            if plan is not None:
                items.insert(0, plan)
            if items:
                kid.days.append((iso, items, _rain(data, iso, entries)))
        kids.append(kid)
    shared_days = []
    for iso in window:
        entries = []
        if iso in weekdays:
            entries = _shown(weekdays[iso]["shared"])
        elif iso in ahead:
            entries = _shown(ahead[iso]["shared"])
        items = _order([make_item(entry, today) for entry in entries])
        if items:
            shared_days.append((iso, items, _rain(data, iso, entries)))
    later = {name: [] for name in [*names, "Begge"]}
    for entry in data.get("later_events", []):
        if not entry["line"]["hidden"]:
            later.setdefault(entry["who"], []).append((entry["date"], make_item(entry["line"], today)))
    _weekend_rows(data, names, kids, shared_days, later, window, today)
    for kid in kids:
        kid.later = later.get(kid.name, [])
    battery = data.get("device", {}).get("battery_percent")
    return Model(board.weekend_day_title({"date": hero_date}), _weather(data, hero_date), _status(data),
                 _status_bold(data), battery, kids, shared_days, later["Begge"], 0,
                 _unread(data, live_messages))


# --- Layout ----------------------------------------------------------------------------

def _prefix_width(item: Item, family: str) -> float:
    if item.box:
        return {"hw": 19, "item": 17, "r": 17}[family]
    if item.chip:
        return text_width(item.chip, "chip") + 8 + 6
    return 4 if item.urgent else 0


def item_width(item: Item, family: str) -> float:
    regular, bold = _styles(family, item)
    return _prefix_width(item, family) + sum(text_width(text, bold if strong else regular)
                                             for text, strong in item.runs) + (4 if item.urgent else 0)


def wrap_item(item: Item, family: str, width: float) -> list[Item]:
    """The item as one or more lines; continuation lines carry no box or chip."""
    if item_width(item, family) <= width:
        return [item]
    regular, bold = _styles(family, item)
    first = width - _prefix_width(item, family) - (4 if item.urgent else 0)
    words = [(word, strong) for text, strong in item.runs for word in re.findall(r"\s*\S+", text)]
    lines, current, used, room = [], [], 0.0, first
    for word, strong in words:
        piece = word if current else word.lstrip()
        extra = text_width(piece, bold if strong else regular)
        if current and used + extra > room:
            lines.append(current)
            current, used, room = [], 0.0, width - 14
            piece = word.lstrip()
            extra = text_width(piece, bold if strong else regular)
        current.append((piece, strong))
        used += extra
    if current:
        lines.append(current)
    result = [Item(item.kind, lines[0], item.chip, item.box, item.urgent)]
    result += [Item(item.kind, runs, urgent=item.urgent) for runs in lines[1:]]
    return result


def right_rows(days, later, folded: int, dropped: int, tight: bool) -> list[Row]:
    """The right column as rows: each day's items (homework in pairs), then later events."""
    rows: list[Row] = []
    width = RIGHT_END - TEXT_X
    for position, (iso, items, rain) in enumerate(days):
        day_rows: list[Row] = []
        pending = list(items)
        while pending:
            item = pending.pop(0)
            if item.kind == "homework" and pending and pending[0].kind == "homework":
                first, second = item_width(item, "r"), item_width(pending[0], "r")
                # Two columns at the tab stop; else the second right after the first.
                tab = TAB if first <= TAB - 12 and second <= width - TAB else round(first + 16)
                if tab + second <= width:
                    day_rows.append(Row([item, pending.pop(0)], tab=tab))
                    continue
            parts = wrap_item(item, "r", width)
            day_rows.append(Row([parts[0]]))
            day_rows += [Row([part], indent=0 if item.urgent else 14) for part in parts[1:]]
        day_rows[0].label = _day_label(iso)
        day_rows[0].gap = position > 0
        if rain:
            if len(day_rows) < 2:
                day_rows.append(Row([]))
            day_rows[1].note = "regn"
        rows += day_rows
    for position, (iso, item) in enumerate(later):
        parts = wrap_item(item, "r", width)
        rows.append(Row([parts[0]], label=_day_label(iso), rule=position == 0 and bool(rows)))
        rows += [Row([part], indent=14) for part in parts[1:]]
    counts = [f"+{dropped} {'punkt' if dropped == 1 else 'punkter'} mere" if dropped else "",
              f"+{folded} senere" if folded else ""]
    if any(counts):
        # One line says what gave way: items left out of the days, and later events.
        rows.append(Row([Item("more", [(" · ".join(count for count in counts if count), False)])],
                        gap=bool(rows) and not (folded and not later), rule=bool(folded and not later and rows)))
    return rows


def _pitch(tight: bool) -> int:
    """Rows on the right are 20 px lines with 2 px between them; none when tightened."""
    return LINE_HEIGHT["r"] + (0 if tight else 2)


def right_height(rows: list[Row], tight: bool) -> int:
    if not rows:
        return 0
    height = 0
    for row in rows:
        height += _pitch(tight)
        if row.gap:
            height += 2 if tight else 5
        if row.rule:
            height += 6 if tight else 10
    return height - (_pitch(tight) - LINE_HEIGHT["r"])


def left_blocks(kid: Kid, tight: bool) -> list[tuple[str, Any]]:
    """(kind, content) blocks of a child's left band, top to bottom."""
    blocks: list[tuple[str, Any, int]] = [("name", kid.name, 0)]
    width = LEFT_END - LEFT_X
    if kid.plan:
        for line in board.wrapped_lines(_ruler(), kid.plan, width, "body", limit=3):
            blocks.append(("plan", line, 0))
    for item in kid.left:
        family = "hw" if item.kind == "homework" else "item"
        for index, part in enumerate(wrap_item(item, family, width)):
            blocks.append((family, part, 14 if index and not item.urgent else 0))
    if kid.message:
        blocks.append(("msg", kid.message, 0))
    return blocks


def left_height(kid: Kid, tight: bool) -> int:
    blocks = left_blocks(kid, tight)
    gap = 2 if tight else 4
    return sum(NAME_HEIGHT if kind == "name" else LINE_HEIGHT[kind] for kind, _, _ in blocks) + gap * (len(blocks) - 1)


def row_heights(model: Model) -> list[int]:
    """Natural heights of the child rows and the Begge row, without the shared leftover."""
    result = []
    for kid in model.kids:
        right = right_rows(kid.days, kid.later, kid.later_folded, kid.dropped_right, model.tight)
        result.append(DIVIDER + max(sum(LEFT_PAD) + left_height(kid, model.tight),
                                    sum(RIGHT_PAD) + right_height(right, model.tight)))
    shared = right_rows(model.shared_days, model.shared_later, model.shared_folded, 0, model.tight)
    result.append(DIVIDER + max(sum(BEGGE_PAD) + 22, sum(BEGGE_PAD) + right_height(shared, model.tight)))
    return result


def _drop(items: list[Item], kinds: set[str], keep=lambda item: False) -> tuple[list[Item], int]:
    kept = [item for item in items if item.kind not in kinds or keep(item)]
    return kept, len(items) - len(kept)


def _sides(kid: Kid, tight: bool) -> tuple[int, int]:
    """The heights of a child's left and right side; only the taller one sets the row."""
    right = right_rows(kid.days, kid.later, kid.later_folded, kid.dropped_right, tight)
    return sum(LEFT_PAD) + left_height(kid, tight), sum(RIGHT_PAD) + right_height(right, tight)


def _right_binds(kid: Kid, tight: bool) -> bool:
    left, right = _sides(kid, tight)
    return right > left


def _drop_right(model: Model, kinds: set[str], *, unless_event_day=False) -> None:
    """Drop `kinds` from the days ahead of children whose right side sets the row."""
    for kid in model.kids:
        if not _right_binds(kid, model.tight):
            continue
        days = []
        for iso, items, rain in kid.days:
            event_day = unless_event_day and any(item.chip for item in items)
            kept, count = _drop(items, set() if event_day else kinds, lambda item: item.urgent)
            kid.dropped_right += count
            if kept:
                days.append((iso, kept, rain))
        kid.days = days


def _drop_left(model: Model, kinds: set[str]) -> None:
    for kid in model.kids:
        if _right_binds(kid, model.tight):
            continue
        kid.left, count = _drop(kid.left, kinds, lambda item: item.urgent)
        kid.dropped_left += count


def _fold(later: list, *, all_of_them: bool) -> tuple[list, int]:
    keep = [] if all_of_them else [(iso, item) for iso, item in later if item.kind == "deadline"]
    return keep, len(later) - len(keep)


def _fold_later(model: Model, *, all_of_them: bool = False) -> None:
    """Later events become '+N senere': for children whose right side sets the row, and Begge."""
    for kid in model.kids:
        if _right_binds(kid, model.tight):
            kid.later, count = _fold(kid.later, all_of_them=all_of_them)
            kid.later_folded += count
    model.shared_later, count = _fold(model.shared_later, all_of_them=all_of_them)
    model.shared_folded += count


def _fold_all_later(model: Model) -> None:
    _fold_later(model, all_of_them=True)


def _last_plan(kid: Kid, only_with_others: bool) -> tuple[int, Item] | None:
    """The farthest day ahead's plan line that may go, as (day index, item)."""
    for index in range(len(kid.days) - 1, -1, -1):
        items = kid.days[index][1]
        plan = next((item for item in items if item.kind == "plan"), None)
        if plan is not None and (not only_with_others or len(items) > 1):
            return index, plan
    return None


def _drop_plan_lines(model: Model, *, only_with_others: bool = False) -> None:
    """Plan lines on the days ahead give way one at a time, the farthest day first,
    from the child whose right side is tallest, until the board fits.

    They retell the weekly plan and are no things to do, so they are not counted in
    `+N punkter mere`. `only_with_others` keeps them on a day that would be empty.
    """
    available = HEIGHT - HEADER_HEIGHT
    while sum(row_heights(model)) > available:
        candidates = [kid for kid in model.kids
                      if _right_binds(kid, model.tight) and _last_plan(kid, only_with_others)]
        if not candidates:
            return
        kid = max(candidates, key=lambda kid: _sides(kid, model.tight)[1])
        index, plan = _last_plan(kid, only_with_others)
        iso, items, rain = kid.days[index]
        items = [item for item in items if item is not plan]
        if items:
            kid.days[index] = (iso, items, rain)
        else:
            del kid.days[index]


def _drop_plan(model: Model) -> None:
    """The plan line goes only where the left side is what makes the row tall."""
    for kid in model.kids:
        if not _right_binds(kid, model.tight):
            kid.plan = ""


def _tighten(model: Model) -> None:
    model.tight = True


DROP_STEPS = (
    # Nothing to do: substitutes and plain information on the coming days, then tomorrow.
    lambda model: _drop_right(model, {"substitute", "info"}),
    lambda model: _drop_left(model, {"substitute", "info"}),
    # Routine things to bring show on the left the evening before; a trip's list stays.
    lambda model: _drop_right(model, {"bring", "optional"}, unless_event_day=True),
    # Later events without a deadline become '+N senere'.
    _fold_later,
    _tighten,
    # The weekly plan's headline on the days ahead: first where the day has other lines.
    lambda model: _drop_plan_lines(model, only_with_others=True),
    _drop_plan,
    _drop_plan_lines,
    _fold_all_later,
    # Before any homework goes: what to bring on event days, then tomorrow's bring list.
    lambda model: _drop_right(model, {"bring", "optional", "more"}),
    lambda model: _drop_left(model, {"bring", "optional"}),
)


def fit_board(model: Model) -> tuple[Model, list[int]]:
    """Drop whole items in `DROP_STEPS` order until the rows fit the panel."""
    available = HEIGHT - HEADER_HEIGHT
    heights = row_heights(model)
    for step in DROP_STEPS:
        if sum(heights) <= available:
            break
        step(model)
        heights = row_heights(model)
    while sum(heights) > available and any(kid.days for kid in model.kids):
        # Last resort: the farthest day of the fullest column gives way, homework too.
        kid = max((kid for kid in model.kids if kid.days), key=lambda kid: len(kid.days))
        iso, items, rain = kid.days.pop()
        kid.dropped_right += sum(1 for item in items if item.kind != "plan")
        heights = row_heights(model)
    spare = max(0, available - sum(heights))
    kids = len(model.kids)
    for index in range(kids):
        heights[index] += spare // kids + (1 if index < spare % kids else 0)
    return model, heights


def layout(data: dict[str, Any], live_messages=None) -> tuple[Model, list[int]]:
    return fit_board(build_model(data, live_messages))


# --- Panel drawing ---------------------------------------------------------------------

def _text(draw, x: float, y: float, value: str, style: str, colour: str) -> float:
    selected, shift = face(style)
    board._draw_protected_text(draw, (round(x), round(y + shift)), value, selected, colour)
    return x + draw.textlength(value, font=selected)


def _envelope(draw, x: int, y: int, colour: str, ground: str, filled: bool) -> None:
    if filled:
        draw.rectangle((x, y, x + 15, y + 11), fill=colour)
        draw.line((x + 1, y + 1, x + 8, y + 7, x + 14, y + 1), fill=ground, width=2)
    else:
        draw.rectangle((x, y, x + 15, y + 11), outline=colour, width=2)
        draw.line((x + 1, y + 1, x + 8, y + 7, x + 14, y + 1), fill=colour, width=2)


def _battery(draw, x: int, y: int, percent: int) -> None:
    draw.rectangle((x, y, x + 19, y + 10), outline=board.BLACK, width=2)
    draw.rectangle((x + 20, y + 3, x + 22, y + 7), fill=board.BLACK)
    inner = max(0, min(15, round(15 * percent / 100)))
    if inner:
        draw.rectangle((x + 3, y + 3, x + 2 + inner, y + 7), fill=board.BLACK)


def _dots(draw, x0: int, x1: int, y: int) -> None:
    for x in range(x0, x1, 3):
        draw.point((x, y), fill=board.BLACK)
        draw.point((x, y + 1), fill=board.BLACK)


def draw_item(draw, x: float, y: float, item: Item, family: str, colour: str, ground: str) -> None:
    """One item at (x, y): its box or category chip, then its runs; yellow behind it when urgent."""
    regular, bold = _styles(family, item)
    size = STYLES[regular][0]
    ink = colour
    if item.urgent:
        width = item_width(item, family)
        draw.rectangle((round(x), round(y + 1), round(x + width), round(y + size + 4)), fill=board.YELLOW)
        ink = board.BLACK
        x += 4
    if item.box:
        side = 13 if family == "hw" else 11
        top = round(y + (size - side) / 2 + 2)
        draw.rectangle((round(x), top, round(x) + side, top + side), outline=ink, width=2)
        x += _prefix_width(item, family)
    elif item.chip:
        chip_width = text_width(item.chip, "chip") + 8
        draw.rectangle((round(x), round(y + 1), round(x + chip_width), round(y + size + 3)), fill=board.BLUE)
        _text(draw, x + 4, y + 2 + (size - 14) / 2, item.chip, "chip", board.WHITE)
        x += chip_width + 6
    for text, strong in item.runs:
        x = _text(draw, x, y, text, bold if strong else regular, ink)


def draw_right_rows(draw, rows: list[Row], top: int, tight: bool) -> None:
    y = top
    for row in rows:
        if row.gap:
            y += 2 if tight else 5
        if row.rule:
            y += 3 if tight else 5
            _dots(draw, RIGHT_X, TEXT_X - 8, y)
            _dots(draw, TEXT_X, RIGHT_END, y)
            y += 3 if tight else 5
        if row.label:
            _text(draw, RIGHT_X, y, row.label, "r_b", board.BLACK)
        if row.note:
            from skoleklar.weather_icons import draw_icon
            draw_icon(draw, "umbrella", RIGHT_X, y + 3, 14, board.ICON_COLOURS)
            _text(draw, RIGHT_X + 18, y + 1, row.note, "chip", board.BLACK)
        for position, item in enumerate(row.items):
            x = TEXT_X + row.indent + (row.tab if position else 0)
            draw_item(draw, x, y, item, "r", board.BLACK, board.PAPER)
        y += _pitch(tight)


def draw_left(draw, kid: Kid, top: int, bottom: int, tight: bool) -> None:
    y = top + LEFT_PAD[0]
    gap = 2 if tight else 4
    for kind, content, indent in left_blocks(kid, tight):
        if kind == "name":
            _text(draw, LEFT_X, y, content, "name", board.WHITE)
            y += NAME_HEIGHT
        elif kind == "plan":
            _text(draw, LEFT_X, y, content, "plan", board.WHITE)
            y += LINE_HEIGHT["plan"]
        elif kind == "msg":
            draw_message(draw, LEFT_X, y, LEFT_END, content, board.WHITE, board.BLACK)
            y += LINE_HEIGHT["msg"]
        else:
            draw_item(draw, LEFT_X + indent, y, content, kind, board.WHITE, board.BLACK)
            y += LINE_HEIGHT[kind]
        y += gap


def header_parts(model: Model) -> tuple[str, str]:
    """The date's style and the weather label beside it.

    The weather keeps its advice ('1–5° Vintertøj'): a long date steps down to the
    smaller face first, then the advice goes, then the weather.
    """
    if not model.weather:
        return "date", ""
    _, temperatures, advice = model.weather
    full = f"{temperatures} {advice}".strip()
    for style, label in (("date", full), ("date_s", full), ("date", temperatures)):
        if 25 + text_width(label, "wx") <= LEFT_END - LEFT_X - text_width(model.day_title, style) - 10:
            return style, label
    return "date", ""


def draw_status(draw, data: dict[str, Any], *, bottom: bool = False) -> None:
    """The update stamp and the board's battery, right-aligned; the same on every view.

    The parents' board has it in the top right. The weekend and the children's day
    view keep their top right for headings and put it in the bottom right instead.
    """
    style = "status_b" if _status_bold(data) else "status"
    y = HEIGHT - 20 if bottom else 14
    x = RIGHT_END
    battery = data.get("device", {}).get("battery_percent")
    if battery is not None:
        label = f"{battery} %"
        x -= text_width(label, style)
        _text(draw, x, y, label, style, board.BLACK)
        x -= 29
        _battery(draw, round(x), y + 3, battery)
        x -= 14
    status = _status(data)
    _text(draw, x - text_width(status, style), y, status, style, board.BLACK)


def status_html(data: dict[str, Any]) -> str:
    """The update stamp and battery as HTML, for any view."""
    battery = data.get("device", {}).get("battery_percent")
    html = f'<span>{_esc(_status(data))}</span>'
    if battery is not None:
        level = max(0, min(15, round(15 * battery / 100)))
        html += ('<span class="pb-battery"><svg class="pb-env" viewBox="0 0 23 11" aria-hidden="true">'
                 '<rect x="1" y="1" width="18" height="9" fill="none" stroke="currentColor" stroke-width="2"/>'
                 '<rect x="20" y="3" width="3" height="5" fill="currentColor"/>'
                 f'<rect x="3" y="3" width="{level}" height="5" fill="currentColor"/></svg>{_esc(battery)} %</span>')
    return f'<div class="pb-status{" stale" if _status_bold(data) else ""}">{html}</div>'


def weekend_activities(data: dict[str, Any]) -> list[tuple[str, str, list[Item]]]:
    """(day label, whose, items) for the weekend cover's activities.

    The parents' board's boxes and labels: `FEST Fødselsdagsfest`, then
    `Medbring udetøj`. An activity the two rows had no room for is counted on the
    last one, `(+1 mere)`.
    """
    today = board.timestamp(data["freshness"]["composed_at"]).astimezone(board.ZONE).date()
    result = []
    for row in data.get("weekend", []):
        items = [make_item(entry, today) for entry in _shown(row["lines"])]
        if items:
            result.append((_day_label(row["date"]), row["who"], items))
    overflow = data.get("weekend_overflow", 0)
    if overflow and result and result[-1][2]:
        last = result[-1][2][-1]
        last.runs = [*last.runs, (f" (+{overflow} mere)", False)]
    return result


def message_lines(data: dict[str, Any], live_messages=None) -> tuple[list[tuple[str, Message]], str]:
    """Each child's latest message, as on the parents' board, and the unread total."""
    lines = []
    for child in data["hero"]["children"]:
        message = _message(data, child["name"], live_messages)
        if message is not None:
            lines.append((child["name"], message))
    return lines, _unread(data, live_messages)


def draw_message(draw, x: float, y: float, end: float, message: Message, ink: str, ground: str,
                 who: str = "") -> None:
    """One message line: envelope (filled when unread), whose, sender: topic, and the date."""
    _envelope(draw, round(x), y + 4, ink, ground, message.unread)
    x += 23
    if who:
        x = _text(draw, x, y, who, "msg_b", ink)
        x = _text(draw, x, y, " · ", "msg", ink)
    label = message.sender + (f": {message.topic}" if message.topic else "")
    _text(draw, x, y, label, "msg", ink)
    if message.when:
        style = "msg_b" if message.unread else "msg"
        _text(draw, end - text_width(message.when, style), y, message.when, style, ink)


def message_html(message: Message, who: str = "") -> str:
    label = message.sender + (f": {message.topic}" if message.topic else "")
    icon = _ENVELOPE.format(_ENVELOPE_FILLED if message.unread else _ENVELOPE_OPEN)
    whose = f"<b>{_esc(who)}</b> · " if who else ""
    return (f'<span class="pb-msg{" unread" if message.unread else ""}">{icon}'
            f'<span>{whose}{_esc(label)}</span><time>{_esc(message.when)}</time></span>'
            ).replace("@INK@", board.HTML_PALETTE["INK"])


def total_html(total: str) -> str:
    """The unread total, with an envelope when there is a count."""
    icon = _ENVELOPE.format(_ENVELOPE_OPEN) if total.endswith("i alt") else ""
    return f'<p class="pb-total">{icon}{_esc(total)}</p>'


def draw_parents_board(draw, data: dict[str, Any], live_messages=None) -> None:
    """The whole parents' board onto a panel image whose left 340 px are already dark."""
    model, heights = layout(data, live_messages)
    # Header: the day and its weather on the dark side; the update stamp and battery on the right.
    date_style, label = header_parts(model)
    _text(draw, LEFT_X, 4 if date_style == "date" else 6, model.day_title, date_style, board.WHITE)
    if label:
        right = LEFT_END - text_width(label, "wx")
        _text(draw, right, 12, label, "wx", board.WHITE)
        from skoleklar.weather_icons import draw_icon
        draw_icon(draw, model.weather[0], round(right - 25), 9, 21, board.HERO_ICON_COLOURS)
    draw_status(draw, data)

    top = HEADER_HEIGHT
    for kid, height in zip(model.kids, heights):
        draw.rectangle((0, top, SPLIT - 1, top + DIVIDER - 1), fill=board.WHITE)
        draw.rectangle((SPLIT, top, WIDTH - 1, top + DIVIDER - 1), fill=board.BLACK)
        draw_left(draw, kid, top + DIVIDER, top + height, model.tight)
        rows = right_rows(kid.days, kid.later, kid.later_folded, kid.dropped_right, model.tight)
        draw_right_rows(draw, rows, top + DIVIDER + RIGHT_PAD[0], model.tight)
        top += height
    draw.rectangle((0, top, SPLIT - 1, top + DIVIDER - 1), fill=board.WHITE)
    draw.rectangle((SPLIT, top, WIDTH - 1, top + DIVIDER - 1), fill=board.BLACK)
    y = top + DIVIDER + BEGGE_PAD[0]
    _text(draw, LEFT_X, y, "Begge", "begge", board.WHITE)
    width = text_width(model.unread, "msg_b")
    _text(draw, LEFT_END - width, y + 2, model.unread, "msg_b", board.WHITE)
    if model.unread.endswith("i alt"):
        _envelope(draw, round(LEFT_END - width - 23), y + 6, board.WHITE, board.BLACK, False)
    shared = right_rows(model.shared_days, model.shared_later, model.shared_folded, 0, model.tight)
    draw_right_rows(draw, shared, y, model.tight)


# --- Browser ----------------------------------------------------------------------------

# What every view shares with the parents' board: the status, category boxes,
# checkboxes and message lines. Added to the weekend and day view pages too.
SHARED_CSS = """
.pb-status{display:flex;justify-content:flex-end;align-items:center;gap:14u;font-size:14u;color:@INK@}
.pb-status.stale{font-weight:700}
.pb-battery{display:flex;align-items:center;gap:6u}
.pb-env{width:16u;height:12u;flex:none}
.pb-battery .pb-env{width:23u;height:11u}
.chip{background:@INDIGO@;color:#fff;font:700 14u/1.2 var(--sans);padding:0 4u;margin-right:6u}
.pb-msg{display:flex;align-items:center;gap:7u;font-size:15u;line-height:20u}
.pb-msg time{margin-left:auto}
.pb-msg.unread time{font-weight:700}
"""

PARENTS_CSS = """
@container (min-width:681px){.dashboard.parents{grid-template-columns:340fr 460fr}}
.dashboard.parents{font-size:15u;line-height:1.33}
.parents .pb-cell{min-width:0}
.parents .pb-dark{background:@INK@;color:@PAGE@}
.parents .pb-head-l{display:flex;justify-content:space-between;align-items:center;gap:8u;padding:4u 14u 5u 10u}
.parents .pb-date{font:700 26u/34u var(--serif);margin:0;white-space:nowrap}
.parents .pb-date.small{font-size:23u}
.parents .pb-wx{display:flex;align-items:center;gap:4u;font:700 15u/1 var(--sans);white-space:nowrap;flex:none}
.parents .pb-wx .wx-icon{width:21u;height:21u}
.parents .pb-head-r{display:flex;justify-content:flex-end;align-items:center;padding:4u 10u 5u 14u}
.parents .pb-left{border-top:2u solid @PAGE@;padding:7u 14u 9u 10u;display:flex;flex-direction:column;gap:4u}
.parents .pb-right{border-top:2u solid @INK@;padding:8u 10u 9u 14u}
.parents .pb-name{font:700 23u/25u var(--serif);margin:0}
.parents .pb-plan{font-size:16u;line-height:21u}
.parents .pb-hw{font-size:18u;line-height:23u}
.parents .pb-item{font-size:16u;line-height:21u}
.parents .box{display:inline-block;width:11u;height:11u;border:2u solid currentColor;margin-right:6u;vertical-align:-1u}
.parents .pb-hw .box{width:13u;height:13u}
.parents .urg{background:@MARK@;color:@INK@;padding:0 4u}
.parents .pb-days{display:grid;grid-template-columns:60u 1fr;column-gap:8u;row-gap:2u;align-content:start}
.parents .pb-days > *{font-size:15u;line-height:20u;min-width:0}
.parents .pb-d{font-weight:700;white-space:nowrap}
.parents .pb-dayplan{font:400 16u/20u var(--serif)}
.parents .pb-note{display:flex;align-items:center;gap:4u;font:700 14u/20u var(--sans)}
.parents .pb-note .wx-icon{width:14u;height:14u}
.parents .pb-gap{margin-top:5u}
.parents .pb-rule{border-top:1.5u dotted @INK@;margin-top:7u;padding-top:4u}
.parents .pb-pair{display:grid;grid-template-columns:166u auto}
.parents .pb-indent{padding-left:14u}
.parents .pb-begge-l{border-top:2u solid @PAGE@;padding:6u 14u 8u 10u;display:flex;justify-content:space-between;align-items:center}
.parents .pb-begge-name{font:700 19u/22u var(--serif)}
.parents .pb-unread{display:flex;align-items:center;gap:7u;font-weight:700}
.parents .pb-begge-r{border-top:2u solid @INK@;padding:6u 10u 8u 14u}
@container (max-width:680px){.dashboard.parents{grid-template-columns:1fr}}
"""

_ENVELOPE = ('<svg class="pb-env" viewBox="0 0 16 12" aria-hidden="true">{}</svg>')
_ENVELOPE_OPEN = '<rect x="1" y="1" width="14" height="10" fill="none" stroke="currentColor" stroke-width="1.5"/>' \
                 '<path d="M1 1l7 6 7-6" fill="none" stroke="currentColor" stroke-width="1.5"/>'
_ENVELOPE_FILLED = '<rect x="0.5" y="0.5" width="15" height="11" fill="currentColor"/>' \
                   '<path d="M1 1l7 6 7-6" fill="none" stroke="@INK@" stroke-width="1.6"/>'


def _esc(value: Any) -> str:
    return board.esc(value)


def item_html(item: Item) -> str:
    if item.kind == "plan":
        return f'<span class="pb-dayplan">{"".join(_esc(text) for text, _ in item.runs)}</span>'
    parts = []
    if item.box:
        parts.append('<span class="box"></span>')
    if item.chip:
        parts.append(f'<span class="chip">{_esc(item.chip)}</span>')
    for text, strong in item.runs:
        parts.append(f"<b>{_esc(text)}</b>" if strong else _esc(text))
    inner = "".join(parts)
    return f'<span class="urg">{inner}</span>' if item.urgent else inner


def rows_html(rows: list[Row], tight: bool) -> str:
    from skoleklar.weather_icons import icon_svg
    cells = []
    for row in rows:
        classes = " ".join(name for name, on in (("pb-gap", row.gap), ("pb-rule", row.rule)) if on)
        label = _esc(row.label)
        if row.note:
            label = f'{icon_svg("umbrella", board.HTML_ICON_COLOURS)}{_esc(row.note)}'
        label_class = "pb-note" if row.note else "pb-d"
        cells.append(f'<span class="{label_class} {classes}">{label}</span>')
        if len(row.items) == 2:
            tab = "" if row.tab == TAB else f' style="grid-template-columns:calc({row.tab}*var(--u)) auto"'
            content = (f'<span class="pb-pair"{tab}><span>{item_html(row.items[0])}</span>'
                       f'<span>{item_html(row.items[1])}</span></span>')
        else:
            content = "".join(item_html(item) for item in row.items)
        indent = " pb-indent" if row.indent else ""
        cells.append(f'<span class="{classes}{indent}">{content}</span>')
    return f'<div class="pb-days">{"".join(cells)}</div>' if cells else ""


def left_html(kid: Kid, tight: bool) -> str:
    parts = []
    for kind, content, indent in left_blocks(kid, tight):
        if kind == "name":
            parts.append(f'<h2 class="pb-name">{_esc(content)}</h2>')
        elif kind == "plan":
            parts.append(f'<span class="pb-plan">{_esc(content)}</span>')
        elif kind == "msg":
            parts.append(message_html(content))
        else:
            parts.append(f'<span class="pb-{kind}{" pb-indent" if indent else ""}">{item_html(content)}</span>')
    return "".join(parts)


def body_html(data: dict[str, Any], live_messages=None) -> str:
    from skoleklar.weather_icons import icon_svg
    model, heights = layout(data, live_messages)
    weather = ""
    date_style, label = header_parts(model)
    if label:
        weather = (f'<span class="pb-wx">{icon_svg(model.weather[0], board.HTML_HERO_ICON_COLOURS)}'
                   f'{_esc(label)}</span>')
    parts = [
        f'<div class="pb-cell pb-dark pb-head-l"><h1 class="pb-date{" small" if date_style == "date_s" else ""}">'
        f'{_esc(model.day_title)}</h1>{weather}</div>',
        f'<div class="pb-cell pb-head-r">{status_html(data)}</div>',
    ]
    for kid in model.kids:
        rows = right_rows(kid.days, kid.later, kid.later_folded, kid.dropped_right, model.tight)
        parts.append(f'<section class="pb-cell pb-dark pb-left">{left_html(kid, model.tight)}</section>')
        parts.append(f'<section class="pb-cell pb-right">{rows_html(rows, model.tight)}</section>')
    shared = right_rows(model.shared_days, model.shared_later, model.shared_folded, 0, model.tight)
    envelope = _ENVELOPE.format(_ENVELOPE_OPEN) if model.unread.endswith("i alt") else ""
    parts.append(f'<section class="pb-cell pb-dark pb-begge-l"><span class="pb-begge-name">Begge</span>'
                 f'<span class="pb-unread">{envelope}{_esc(model.unread)}</span></section>')
    parts.append(f'<section class="pb-cell pb-begge-r">{rows_html(shared, model.tight)}</section>')
    tracks = " ".join([*(f"minmax(min-content,{height}fr)" for height in heights[:-1]), "auto"])
    css = SHARED_CSS + PARENTS_CSS + f".dashboard.parents{{grid-template-rows:auto {tracks}}}\n"
    return "".join(parts).replace("@INK@", board.HTML_PALETTE["INK"]), css
