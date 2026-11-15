"""Draw the children's day view (`day_view` in the state) in the browser and on the panel.

Left, as at the weekend: the day, its weather, the season's house artwork and a
short list of what to remember. Right, one row per child: the lessons as tiles on
the day's shared bell schedule, a substitute named on its tile and a cancelled
lesson struck through, then the weekly plan retold for that day. Only a day that
ends earlier than its timetable gets the straw marker, on its `Fri kl.`.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from skoleklar import dashboard_renderer as board
from skoleklar.day_view import SHORT_SUBJECTS, clothing

SOFT_HYPHEN = "­"
CHANGES = (None, "vikar", "aflyst")
LABELS = ("I dag", "I morgen")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# What a remember line says without its board marker, in a child's words.
_MARKERS = (("Medbring:", "{}"), ("Besked:", "{}"), ("Lektie:", "Lektie: {}"),
            ("Valgfrit:", "{} (hvis du vil)"), ("Afklar:", "{}"))


def validate_day_view(data: dict[str, Any]) -> None:
    """Allowlist the `day_view` field; called by the board's own `validate`."""
    view = data["day_view"]
    if data["schema_version"] != 2 or "weather" in data:
        raise ValueError("day view requires schema version 2 and no weekend weather")
    if not isinstance(view, dict) or set(view) != {"date", "label", "slots", "children"}:
        raise ValueError("unknown or missing day view fields")
    try:
        day = date.fromisoformat(view["date"])
    except (TypeError, ValueError) as error:
        raise ValueError("day view date must be ISO format") from error
    if day.weekday() >= 5:
        raise ValueError("day view is a school day")
    if view["label"] not in LABELS:
        raise ValueError("day view label must be 'I dag' or 'I morgen'")
    slots = view["slots"]
    if (not isinstance(slots, list) or not 1 <= len(slots) <= 10
            or any(not isinstance(slot, str) or not _HHMM.match(slot) for slot in slots)
            or slots != sorted(set(slots))):
        raise ValueError("day view slots are one to ten ordered HH:MM times")
    names = [child["name"] for child in data["hero"]["children"]]
    children = view["children"]
    if not isinstance(children, list) or [
            child.get("name") if isinstance(child, dict) else None for child in children] != names:
        raise ValueError("day view children must match the hero")
    for child in children:
        if set(child) != {"name", "lessons"} or not isinstance(child["lessons"], list) \
                or len(child["lessons"]) > 10:
            raise ValueError("day view child has a name and at most ten lessons")
        previous_end = "00:00"
        for lesson in child["lessons"]:
            if not isinstance(lesson, dict) or set(lesson) != {"start", "end", "subject", "change", "note"}:
                raise ValueError("unknown or missing day view lesson fields")
            if lesson["start"] not in slots or not isinstance(lesson["end"], str) \
                    or not _HHMM.match(lesson["end"]) or lesson["end"] <= lesson["start"] \
                    or lesson["start"] < previous_end:
                raise ValueError("day view lessons are ordered and start on a slot")
            previous_end = lesson["end"]
            subject, note = lesson["subject"], lesson["note"]
            if (not isinstance(subject, str) or not 1 <= len(subject) <= 32
                    or any(ord(c) < 32 for c in subject)):
                raise ValueError("day view subject must be short single-line text")
            if lesson["change"] not in CHANGES:
                raise ValueError("unknown day view change")
            if (not isinstance(note, str) or len(note) > 24 or any(ord(c) < 32 for c in note)
                    or bool(note) != (lesson["change"] == "vikar")):
                raise ValueError("only a substitute carries a note: the first name")


def clock_label(value: str) -> str:
    """'09:00' -> '9.00', as a Danish timetable writes it."""
    hours, minutes = value.split(":")
    return f"{int(hours)}.{minutes}"


def plain(value: str) -> str:
    return value.replace(SOFT_HYPHEN, "")


def view_weather(data: dict[str, Any]) -> dict[str, Any] | None:
    return board.day_weather(data).get(data["day_view"]["date"])


def ends_earlier(child: dict[str, Any]) -> bool:
    """The day ends before its timetable does, because its last lessons are cancelled.

    That is the one change parents must act on, so only it gets the yellow marker.
    """
    held = [lesson for lesson in child["lessons"] if lesson["change"] != "aflyst"]
    return bool(held) and max(lesson["end"] for lesson in held) < max(
        lesson["end"] for lesson in child["lessons"])


def ends_label(child: dict[str, Any]) -> str:
    """'Fri kl. 13.45' after the child's last lesson that takes place."""
    held = [lesson for lesson in child["lessons"] if lesson["change"] != "aflyst"]
    return f"Fri kl. {clock_label(held[-1]['end'])}" if held else ""


def day_summary(data: dict[str, Any], name: str) -> tuple[str, str] | None:
    """The weekly plan's headline and text for this child and day, when written."""
    day = data["day_view"]["date"]
    for entry in data.get("day_summaries", []):
        if entry["date"] != day:
            continue
        for child in entry["children"]:
            if child["name"] == name and child["status"] == "ok":
                return child["headline"], child["text"]
    return None


def remember_text(line: str) -> str:
    text = " ".join(str(line).split())
    if not text or text.casefold().startswith("vikar"):
        return ""  # A substitute is on the lesson tile.
    for marker, form in _MARKERS:
        if text.casefold().startswith(marker.casefold()):
            rest = text[len(marker):].strip()
            text = form.format(rest) if rest else ""
            break
    return text[:1].upper() + text[1:]


def remember_items(data: dict[str, Any]) -> list[tuple[str, str]]:
    """(who, what) to remember: clothes for the weather, then each child's lines.

    The same thing for both children is one `Begge` item.
    """
    items: list[list[str]] = []
    for child in data["hero"]["children"]:
        for line in board.display_lines(child):
            text = remember_text(line)
            if not text:
                continue
            same = next((item for item in items
                         if item[1].casefold() == text.casefold() and item[0] != child["name"]), None)
            if same:
                same[0] = "Begge"
            elif not any(item[1].casefold() == text.casefold() for item in items):
                items.append([child["name"], text])
    card = next((day for day in data.get("weekdays", []) if day["date"] == data["day_view"]["date"]), None)
    if card and card.get("shared"):
        text = re.sub(r"^\s*Begge:\s*", "", card["shared"])
        if text and not any(item[1].casefold() == text.casefold() for item in items):
            items.append(["Begge", text[:1].upper() + text[1:]])
    wear = clothing(view_weather(data))
    return ([("Tøj", wear)] if wear else []) + [(who, what) for who, what in items]


def view_title(data: dict[str, Any]) -> str:
    return board.weekend_day_title({"date": data["day_view"]["date"]})


# ----------------------------------------------------------------------------- HTML

DAY_VIEW_CSS = """
/* The panel's own split: the artwork column is 340 wide, the timetable 460. */
@container (min-width:681px){.dashboard.day-view{grid-template-columns:340fr 460fr}}
.kids .kid-head{display:flex;justify-content:space-between;align-items:center;margin:0 14u 0 10u}
.kids .wx-now{display:flex;align-items:center;gap:6u;font:700 15u/1 var(--sans)}
.kids .wx-now .wx-icon{width:28u;height:28u}
.kids h1{margin-bottom:12u}
.remember{margin:0 14u 0 10u;border-top:1px solid @INK@;padding-top:9u}
.remember h3{margin:0 0 7u;color:@INK@;font-size:11u;letter-spacing:.14em;text-transform:uppercase}
.remember ul{list-style:none;margin:0;padding:0;display:grid;gap:5u}
.remember li{display:grid;grid-template-columns:13u minmax(0,1fr);column-gap:9u;align-items:start;
 font:14u/1.3 var(--sans)}
.remember li::before{content:"";width:11u;height:11u;margin-top:3u;border:1.5px solid @INK@;border-radius:1px}
.remember li b{font-weight:700}
.remember .none{display:block;font:italic 15u/1.3 var(--serif)}
.kids .hero-foot .updated{display:block;opacity:.7}
/* One column per lesson slot, shared by both children through subgrids, so their
   rows line up in time. The columns are fractions weighted by their names (see
   column_weights), so they always fit the panel. */
.dayplan{padding:16u 10u 12u 14u;min-width:0;display:grid;grid-template-rows:1fr 1fr;column-gap:4u;row-gap:10u;align-content:start}
.kid{grid-column:1/-1;display:grid;grid-template-columns:subgrid;grid-template-rows:auto auto 1fr;align-content:start;
 border-top:2px solid @INK@;padding-top:7u;min-width:0}
.kid header{grid-column:1/-1;display:flex;justify-content:space-between;align-items:baseline;gap:8u}
.kid h2{font:700 24u/1.1 var(--serif);margin:0}
.kid .ends{font:700 11u var(--sans);color:@INK@;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap}
.kid .ends.earlier{background:@MARK@;padding:0 4u}
.dayplan .pb-status{grid-column:1/-1;align-self:end}
.lessons{grid-column:1/-1;list-style:none;margin:7u 0 0;padding:0;display:grid;grid-template-columns:subgrid}
.lessons li{background:@PAGE@;border:1px solid #131a2638;padding:5u 5u 6u;min-width:0;
 display:flex;flex-direction:column;gap:3u}
.lessons small{font:500 10u/1.2 var(--sans);color:@SLATE@}
/* Names break at their soft hyphen first; only a day too full for that breaks a word. */
.lessons b,.lessons small{overflow-wrap:break-word}
.lessons b{font:700 13u/1.15 var(--sans);hyphens:manual}
.lessons .vikar{border-color:@INK@}
.lessons .vikar small,.lessons .aflyst small{color:@INK@;font-weight:700}
.lessons .aflyst{background:transparent;border-style:dashed;border-color:@INK@}
.lessons .aflyst b{text-decoration:line-through}
.lessons-none{grid-column:1/-1;margin:8u 0 0;font:italic 13u/1.3 var(--serif)}
.plan{grid-column:1/-1;margin:8u 0 0;font:13u/1.4 var(--serif)}
.plan b{display:block;font:700 14u/1.25 var(--serif);margin-bottom:2u}
@container (max-width:680px){.dayplan{grid-template-rows:none;row-gap:20px}}
/* A phone is too narrow for a day's slots side by side: the tiles wrap instead. */
@container (max-width:460px){
 .dayplan,.kid{display:block}
 .kid+.kid{margin-top:20px}
 .lessons{grid-template-columns:repeat(auto-fill,minmax(88px,1fr));gap:4px}
 .lessons li{grid-column:auto!important}
}
"""


def _subject_html(subject: str) -> str:
    # A break after a slash needs no hyphen; elsewhere the soft hyphen shows one.
    return board.esc(subject).replace("/" + SOFT_HYPHEN, "/<wbr>")


def _lessons_html(view: dict[str, Any], child: dict[str, Any]) -> str:
    if not child["lessons"]:
        return '<p class="lessons-none">Ingen lektioner i skemaet</p>'
    slots = view["slots"]
    tiles = []
    for lesson in child["lessons"]:
        first = slots.index(lesson["start"]) + 1
        last = max(index for index, slot in enumerate(slots) if slot < lesson["end"]) + 2
        if lesson["change"] == "vikar":
            head = f'Vikar {board.esc(lesson["note"])}'
        elif lesson["change"] == "aflyst":
            head = "Aflyst"
        else:
            head = clock_label(lesson["start"])
        tone = f' class="{lesson["change"]}"' if lesson["change"] else ""
        tiles.append(f'<li{tone} style="grid-column:{first}/{last}">'
                     f'<small>{head}</small><b>{_subject_html(lesson["subject"])}</b></li>')
    return '<ol class="lessons">' + "".join(tiles) + "</ol>"


def body_html(data: dict[str, Any]) -> str:
    """The panel's two sections for the children's view."""
    from skoleklar.weekend_art import artwork_data_uri, select_artwork
    view = data["day_view"]
    weather = view_weather(data)
    preset = select_artwork(weather or {"date": view["date"], "icon": "partly"})
    weather_html = ""
    if weather:
        weather_html = (f'<div class="wx-now">{board.icon_svg(weather["icon"], board.HTML_ICON_COLOURS)}'
                        f'{board.esc(board.temperature_range(weather))}</div>')
    items = "".join(f'<li><span><b>{board.esc(who)}</b> · {board.esc(what)}</span></li>'
                    for who, what in remember_items(data))
    remember = f"<ul>{items}</ul>" if items else '<span class="none">Ikke noget særligt i dag</span>'
    # Old data is said once, in bold in the status, as on the parents' board.
    stale = ""
    hero = (
        f'<section class="hero weekend-art kids"><div class="hero-top">{stale}'
        f'<div class="kid-head"><div class="state">{board.esc(view["label"])}</div>{weather_html}</div>'
        f'<h1>{board.esc(view_title(data))}</h1>'
        f'<figure class="wx-cover" data-artwork="{preset}"><img src="{artwork_data_uri(preset)}" '
        'width="680" height="400" alt="Huset og haven i årstidens vejr"></figure>'
        f'<section class="remember"><h3>Husk</h3>{remember}</section></div>'
        '<div class="hero-foot"></div></section>'
    )
    rows = []
    for child in view["children"]:
        ends = ends_label(child)
        summary = day_summary(data, child["name"])
        plan = (f'<p class="plan"><b>{board.esc(summary[0])}</b>{board.esc(summary[1])}</p>'
                if summary else "")
        earlier = " earlier" if ends_earlier(child) else ""
        rows.append(
            f'<article class="kid"><header><h2>{board.esc(child["name"])}</h2>'
            + (f'<span class="ends{earlier}">{board.esc(ends)}</span>' if ends else "")
            + f'</header>{_lessons_html(view, child)}{plan}</article>'
        )
    from skoleklar.parents_board_render import status_html
    columns = "grid-template-columns:" + " ".join(f"minmax(0,{weight}fr)" for weight in column_weights(view))
    status = status_html({**data, "review_count": 0})
    return hero + f'<section class="dayplan" style="{columns}">{"".join(rows)}{status}</section>'


def _units(text: str, size: float) -> float:
    """A generous width of bold sans `text` at `size` design units."""
    narrow, wide = set("iljtfr.,:;/-'|"), set("mwMWÆæ")
    return size * sum(0.36 if c in narrow else 0.9 if c in wide else
                      0.74 if c.isupper() or c.isdigit() else 0.6 for c in text)


def _name_weight(lesson: dict[str, Any]) -> float:
    """A tile's width need in design units: its name on one line, or for a long name
    its widest piece between soft hyphens and spaces; then the smaller head line."""
    subject = lesson["subject"]
    whole = _units(plain(subject), 13)
    pieces = [piece + ("-" if subject.find(piece + SOFT_HYPHEN) >= 0 and not piece.endswith("/") else "")
              for piece in re.split(f"[{SOFT_HYPHEN} ]", subject)]
    name = whole if whole <= 72 else max(_units(piece, 13) for piece in pieces)
    head = "Vikar" if lesson["change"] == "vikar" else (
        "Aflyst" if lesson["change"] == "aflyst" else clock_label(lesson["start"]))
    return max(name, _units(head, 10)) + 12


def column_weights(view: dict[str, Any]) -> list[int]:
    """Fractions for the browser's slot columns, from the names they hold.

    Only proportions come from this estimate: fractions always fit the panel, and
    a name the estimate underrates wraps at its soft hyphen instead of clipping.
    The panel measures real glyphs instead (`column_edges`).
    """
    slots = view["slots"]
    need = [30.0] * len(slots)
    lessons = [lesson for child in view["children"] for lesson in child["lessons"]]
    for lesson in sorted(lessons, key=lambda item: _span(slots, item)[1] - _span(slots, item)[0]):
        first, last = _span(slots, lesson)
        missing = _name_weight(lesson) - sum(need[first:last + 1]) - 4 * (last - first)
        if missing > 0:
            for index in range(first, last + 1):
                need[index] += missing / (last - first + 1)
    return [max(1, round(value)) for value in need]


# ----------------------------------------------------------------------------- Panel

# The frame's white mat is the margin, as on the parents' board.
LEFT, RIGHT = board.PANEL_SPLIT_X + 14, board.WIDTH - 10
TEXT_LEFT, TEXT_END = 10, board.PANEL_SPLIT_X - 14
TILE_GAP = 4


def _width(draw, value: str, style: str) -> float:
    return draw.textlength(value, font=board.styled_font(style)[0])


def subject_lines(draw, subject: str, width: int, style: str = "small_b") -> list[str]:
    """The subject on one line, else broken at its soft hyphen or spaces, else shortened."""
    full = plain(subject)
    if _width(draw, full, style) <= width:
        return [full]
    if SOFT_HYPHEN in subject:
        head, tail = subject.split(SOFT_HYPHEN, 1)
        lines = [head if head.endswith("/") else head + "-", plain(tail)]
        if all(_width(draw, line, style) <= width for line in lines):
            return lines
    words = board.wrapped_lines(draw, full, width, style, limit=2)
    if " ".join(words) == full and all(_width(draw, line, style) <= width for line in words):
        return words
    short = SHORT_SUBJECTS.get(subject)
    if short and _width(draw, short, style) <= width:
        return [short]
    return [board.fit(draw, full, width, board.styled_font(style)[0])]


def _tile_head(lesson: dict[str, Any]) -> tuple[str, str]:
    if lesson["change"] == "vikar":
        return f"Vikar {lesson['note']}", "micro_b"
    if lesson["change"] == "aflyst":
        return "Aflyst", "micro_b"
    return clock_label(lesson["start"]), "micro"


def _span(slots: list[str], lesson: dict[str, Any]) -> tuple[int, int]:
    return (slots.index(lesson["start"]),
            max(index for index, slot in enumerate(slots) if slot < lesson["end"]))


def _tile_need(draw, lesson: dict[str, Any]) -> float:
    """The narrowest tile that shows the head and the full subject, on two lines at most."""
    subject = lesson["subject"]
    forms = [[plain(subject)]]
    if SOFT_HYPHEN in subject:
        head, tail = subject.split(SOFT_HYPHEN, 1)
        forms.append([head if head.endswith("/") else head + "-", plain(tail)])
    words = plain(subject).split()
    if len(words) > 1:
        middle = max(range(1, len(words)), key=lambda cut: -abs(
            _width(draw, " ".join(words[:cut]), "small_b") - _width(draw, " ".join(words[cut:]), "small_b")))
        forms.append([" ".join(words[:middle]), " ".join(words[middle:])])
    text = min(max(_width(draw, line, "small_b") for line in form) for form in forms)
    head, style = _tile_head(lesson)
    return 10 + max(text, min(_width(draw, head, style), 80))


def column_edges(draw, view: dict[str, Any]) -> list[tuple[int, int]]:
    """Left and right x of each slot column, shared by both children's rows.

    Each column gets what its tiles need, a tile across several slots shares its
    need between them, and the spare width is spread evenly. When the needs do not
    fit, every column shrinks in proportion and long names fall back to short forms.
    """
    slots = view["slots"]
    count = len(slots)
    usable = RIGHT - LEFT - TILE_GAP * (count - 1)
    need = [36.0] * count
    lessons = [lesson for child in view["children"] for lesson in child["lessons"]]
    for lesson in sorted(lessons, key=lambda item: _span(slots, item)[1] - _span(slots, item)[0]):
        first, last = _span(slots, lesson)
        have = sum(need[first:last + 1]) + TILE_GAP * (last - first)
        missing = _tile_need(draw, lesson) - have
        if missing > 0:
            for index in range(first, last + 1):
                need[index] += missing / (last - first + 1)
    total = sum(need)
    widths = ([width + (usable - total) / count for width in need] if total <= usable
              else [width * usable / total for width in need])
    edges, x = [], float(LEFT)
    for width in widths:
        edges.append((round(x), round(x + width)))
        x += width + TILE_GAP
    return edges


def _wrap(draw, text: str, first: float, rest: float, style: str) -> list[str]:
    """Greedy word wrap whose first line is `first` wide and the others `rest`."""
    words, lines = text.split(), []
    while words:
        width = first if not lines else rest
        line = words.pop(0)
        while words and _width(draw, line + " " + words[0], style) <= width:
            line += " " + words.pop(0)
        lines.append(line)
    return lines


def _draw_remember(draw, data: dict[str, Any], top: int, bottom: int) -> None:
    left, end = TEXT_LEFT, TEXT_END
    text_x, width = left + 20, end - left - 20
    draw.line((left, top, end, top), fill=board.BLACK, width=1)
    board.draw_text(draw, (left, top + 8), "HUSK", "micro_b", board.BLACK)
    items = remember_items(data)
    y, pitch = top + 28, 18
    if not items:
        board.draw_text(draw, (left, y), "Ikke noget særligt i dag", "body", board.BLACK, end - left)
        return
    for index, (who, what) in enumerate(items):
        offset = _width(draw, who, "small_b") + _width(draw, " · ", "small")
        lines = _wrap(draw, what, width - offset, width, "small")[:2]
        remaining = len(items) - index
        if y + len(lines) * pitch > bottom or (remaining > 1 and y + len(lines) * pitch + pitch > bottom):
            board.draw_text(draw, (text_x, y), f"+{remaining} mere", "small_b", board.BLACK, width)
            return
        draw.rectangle((left, y + 2, left + 11, y + 13), outline=board.BLACK, width=1)
        board.draw_text(draw, (text_x, y), who, "small_b", board.BLACK)
        board.draw_text(draw, (text_x + round(_width(draw, who, "small_b")), y), " · ", "small", board.BLACK)
        for row, line in enumerate(lines):
            x = text_x + round(offset) if row == 0 else text_x
            board.draw_text(draw, (x, y + row * pitch), line, "small", board.BLACK, end - x)
        y += len(lines) * pitch + 5


def _draw_child(draw, view: dict[str, Any], child: dict[str, Any], edges: list[tuple[int, int]],
                summary: tuple[str, str] | None, top: int, bottom: int) -> None:
    draw.line((LEFT, top, RIGHT, top), fill=board.BLACK, width=2)
    ends = ends_label(child).upper()
    ends_width = round(_width(draw, ends, "micro_b")) if ends else 0
    board.draw_text(draw, (LEFT, top + 8), child["name"], "name", board.BLACK, RIGHT - LEFT - ends_width - 18)
    if ends and ends_earlier(child):
        # An earlier finish is time-urgent: the straw marker, as on the parents' board.
        board.draw_marked_text(draw, (RIGHT - ends_width - 6, top + 15), ends, "micro_b")
    elif ends:
        board.draw_text(draw, (RIGHT - ends_width, top + 15), ends, "micro_b", board.BLACK)
    tiles_top = top + 42
    tiles = []
    for lesson in child["lessons"]:
        first, last = _span(view["slots"], lesson)
        x0, x1 = edges[first][0], edges[last][1]
        tiles.append((lesson, x0, x1, subject_lines(draw, lesson["subject"], x1 - x0 - 10)))
    rows = max((len(lines) for *_, lines in tiles), default=1)
    tile_height = 30 + rows * 17
    if not tiles:
        board.draw_text(draw, (LEFT, tiles_top + 4), "Ingen lektioner i skemaet", "small", board.BLACK, RIGHT - LEFT)
        tile_height = 24
    for lesson, x0, x1, lines in tiles:
        box = (x0, tiles_top, x1, tiles_top + tile_height)
        # A substitute is named on the tile, not marked: yellow is for time urgency only.
        draw.rectangle(box, outline=board.BLACK, width=2 if lesson["change"] == "aflyst" else 1)
        head, head_style = _tile_head(lesson)
        board.draw_text(draw, (x0 + 5, tiles_top + 5), head, head_style, board.BLACK, x1 - x0 - 10)
        for row, line in enumerate(lines):
            y = tiles_top + 24 + row * 17
            board.draw_text(draw, (x0 + 5, y), line, "small_b", board.BLACK, x1 - x0 - 10)
            if lesson["change"] == "aflyst":
                width = round(_width(draw, line, "small_b"))
                draw.line((x0 + 5, y + 9, x0 + 5 + width, y + 9), fill=board.BLACK, width=2)
    if summary is None:
        return
    y = tiles_top + tile_height + 10
    width = RIGHT - LEFT
    board.draw_text(draw, (LEFT, y), summary[0], "body_b", board.BLACK, width)
    y += 21
    pitch = 18
    capacity = max(0, (bottom - y) // pitch)
    lines = _wrap(draw, summary[1], width, width, "small")
    if len(lines) > capacity:
        lines = lines[:capacity]
        if lines:
            lines[-1] = board.fit(draw, lines[-1] + " …", width, board.styled_font("small")[0])
    for row, line in enumerate(lines):
        board.draw_text(draw, (LEFT, y + row * pitch), line, "small", board.BLACK, width)


def draw_day_view(draw, data: dict[str, Any]) -> None:
    """Both panels except the artwork, which is pasted after panel conversion."""
    view = data["day_view"]
    draw.rectangle((0, 0, board.PANEL_SPLIT_X - 1, board.HEIGHT), fill=board.PAPER)
    board.draw_text(draw, (TEXT_LEFT, 18), view["label"].upper(), "small_b", board.BLACK)
    weather = view_weather(data)
    if weather:
        label = board.temperature_range(weather)
        width = round(_width(draw, label, "body_b"))
        board.draw_icon(draw, weather["icon"], TEXT_END - width - 34, 10, 28, board.ICON_COLOURS)
        board.draw_text(draw, (TEXT_END - width, 19), label, "body_b", board.BLACK)
    board.draw_text(draw, (TEXT_LEFT, 39), view_title(data), "hero", board.BLACK, TEXT_END - TEXT_LEFT)
    _draw_remember(draw, data, 296, board.HEIGHT - 12)
    # The bottom right keeps room for the update stamp and battery.
    halves = ((16, 236), (246, board.HEIGHT - 26))
    edges = column_edges(draw, view)
    for child, (top, bottom) in zip(view["children"], halves):
        _draw_child(draw, view, child, edges, day_summary(data, child["name"]), top, bottom)


def panel_artwork_preset(data: dict[str, Any]) -> str:
    from skoleklar.weekend_art import select_artwork
    view = data["day_view"]
    return select_artwork(view_weather(data) or {"date": view["date"], "icon": "partly"})
