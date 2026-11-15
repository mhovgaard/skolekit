#!/usr/bin/env python3
"""Render one privacy-filtered school dashboard to indexed PNG and static HTML."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import re
from pathlib import Path
from typing import Any

from datetime import date, datetime, timezone
from functools import lru_cache

from skoleklar.dashboard_timing import ZONE, dashboard_focus

from skoleklar.weather_icons import CELL_ICONS, FLAG_ICONS, VERDICT_ICONS, draw_icon, icon_svg

WIDTH, HEIGHT = 800, 480
# "Aizome" palette: blue-black ink on fog paper with one indigo accent. Red is
# never used; anything that needs attention gets a straw marker behind ink text.
HTML_PALETTE = {
    "INK": "#131A26", "PAGE": "#F6F7F8", "PAPER": "#EBEEF0", "INDIGO": "#2B4C8C",
    "ICE": "#A3B9DC", "MARK": "#E6D584", "SUN": "#C7A640", "MIST": "#DCE2E8", "SLATE": "#7B8794",
}
# The panel has six fixed pigments, so the PNG is drawn directly in the colours
# those pigments have on the panel. Flat fills then stay flat instead of being
# dithered towards a browser colour the panel cannot show.
BLACK = "#191E21"
WHITE = "#E8E8E8"
PAPER = WHITE
YELLOW = "#EFDE44"
BLUE = "#2157BA"

# Waveshare 7.3-inch Spectra 6 / EL073TF1 pixel codes. Code 0x4 is reserved by
# the controller and must never occur in an image sent to the panel. The PNG
# palette therefore deliberately has a hole at index 4.
PANEL_NOMINAL_RGB = {
    0x0: (0x00, 0x00, 0x00),
    0x1: (0xFF, 0xFF, 0xFF),
    0x2: (0xFF, 0xFF, 0x00),
    0x3: (0xFF, 0x00, 0x00),
    0x5: (0x00, 0x00, 0xFF),
    0x6: (0x00, 0xFF, 0x00),
}
PANEL_APPEARANCE_RGB = {
    0x0: (0x19, 0x1E, 0x21),
    0x1: (0xE8, 0xE8, 0xE8),
    0x2: (0xEF, 0xDE, 0x44),
    0x3: (0xB2, 0x13, 0x18),
    0x5: (0x21, 0x57, 0xBA),
    0x6: (0x12, 0x5F, 0x20),
}
PANEL_COLOUR_NAMES = {
    "black": 0x0, "white": 0x1, "yellow": 0x2,
    "red": 0x3, "blue": 0x5, "green": 0x6,
}
# Pigments the design may put on the panel: no red, and green is unused.
PANEL_DESIGN_CODES = (0x0, 0x1, 0x2, 0x5)
DITHER_MODES = ("crisp", "hybrid", "full")
PANEL_SPLIT_X = 340
DITHER_STRENGTH = 0.78


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


# Coloured pixels that are hard-mapped (text and its edges) snap to the nearest
# of these, so antialiasing can never introduce a pigment the design avoids.
DESIGN_PANEL_CODES = {
    _rgb(BLACK): 0x0,
    _rgb(WHITE): 0x1,
    _rgb(YELLOW): 0x2,
    _rgb(BLUE): 0x5,
}


def _icon_colours(ink: str, ground: str, sun: str, rain: str) -> dict[str, str]:
    return {"ink": ink, "ground": ground, "edge": ink, "cloud": ground, "sun": sun,
            "rain": rain, "leaf": ink, "warm": sun, "alert": ink}


# Weather glyphs per surface: the panel's paper and dark hero, then the same two in HTML.
ICON_COLOURS = _icon_colours(BLACK, PAPER, YELLOW, BLUE)
HERO_ICON_COLOURS = _icon_colours(WHITE, BLACK, YELLOW, WHITE)
HTML_ICON_COLOURS = _icon_colours(HTML_PALETTE["INK"], HTML_PALETTE["PAPER"],
                                  HTML_PALETTE["SUN"], HTML_PALETTE["INDIGO"])
HTML_HERO_ICON_COLOURS = _icon_colours(HTML_PALETTE["PAGE"], HTML_PALETTE["INK"],
                                       HTML_PALETTE["SUN"], HTML_PALETTE["ICE"])
WEATHER_FIRST_HOUR, WEATHER_LAST_HOUR = 6, 21


@lru_cache(maxsize=16)
def font(size: int, bold: bool = False, serif: bool = False) -> ImageFont.ImageFont:
    from PIL import ImageFont

    # Bundled faces draw the same panel on a Mac, in the cloud image and on GitHub:
    # Newsreader keeps headlines identical to the HTML, and Arimo has Arial's widths,
    # the sans the HTML asks for. Pillow reads WOFF2 when FreeType has Brotli; without
    # it a system sans below is used, and wider faces such as DejaVu fit less text.
    face = "newsreader" if serif else "arimo"
    bundled = Path(__file__).with_name("fonts") / f"{face}-latin-{700 if bold else 400}.woff2"
    try:
        return ImageFont.truetype(str(bundled), size=size)
    except OSError:
        pass
    names = ["Arial Bold.ttf", "DejaVuSans-Bold.ttf"] if bold else ["Arial.ttf", "DejaVuSans.ttf"]
    roots = (
        Path("/System/Library/Fonts/Supplemental"),
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/local/share/fonts"),
    )
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


FONT_SPECS = {
    # At the panel's 127 ppi, type below about 14 px is difficult to read at the
    # intended viewing distance. Do not shrink meaningful content below this.
    "nano": (14, False),
    # The update stamp is a deliberate footnote below that floor; staleness
    # warnings switch back to a readable size.
    "foot": (11, False),
    "micro": (14, False), "micro_b": (14, True), "small": (15, False),
    "small_b": (15, True), "body": (16, False), "body_b": (16, True),
    "section": (21, True, True), "name": (22, True, True), "temp": (24, True), "temp_s": (21, True),
    "hero": (34, True, True),
}


def styled_font(style: str) -> tuple[ImageFont.ImageFont, int]:
    """The face for a style and the y offset that keeps its baseline where sans would be."""
    spec = FONT_SPECS[style]
    selected = font(*spec)
    if len(spec) < 3:
        return selected, 0
    return selected, font(*spec[:2]).getmetrics()[0] - selected.getmetrics()[0]


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps require a timezone")
    return result


def valid_participant_label(value: Any) -> bool:
    """Accept `First I.` for any uppercase Unicode letter used in a real name."""
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"[^\s.]{1,30} ([^\W\d_])\.", value)
    return bool(match and match.group(1).isupper())


def is_stale(data: dict[str, Any]) -> bool:
    fresh = data.get("freshness")
    return bool(fresh and (fresh["status"] != "fresh" or
                timestamp(fresh["expires_at"]) <= datetime.now(timezone.utc)))


def freshness_label(data: dict[str, Any]) -> str:
    prefix = "ÆLDRE / UFULDSTÆNDIGE DATA · " if is_stale(data) else ""
    fresh = data.get("freshness")
    if not fresh:
        return prefix + data["updated_label"]
    value = timestamp(fresh["source_updated_at"])
    months = ("jan.", "feb.", "mar.", "apr.", "maj", "jun.",
              "jul.", "aug.", "sep.", "okt.", "nov.", "dec.")
    return prefix + f"Opdateret {value.day}. {months[value.month - 1]} {value.year} kl. {value:%H:%M}"


def validate(data: dict[str, Any]) -> None:
    """Allowlist the complete device state; do not accept hidden source payloads."""
    def obj(value, allowed, required):
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        if set(value) - set(allowed) or set(required) - set(value):
            raise ValueError("unknown or missing dashboard fields")

    def line(value, limit=160, label="display"):
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
            length = len(value) if isinstance(value, str) else "non-string"
            raise ValueError(
                "display strings must be bounded single-line text: "
                f"{label} length {length} exceeds {limit} or contains control characters"
            )

    def integer(value):
        if type(value) is not int or value < 0:
            raise ValueError("counts must be non-negative integers")

    def kinds(child):
        """The parents' board's per-line kinds, when present: one known kind per line."""
        from skoleklar.parents_board import KINDS
        if "kinds" in child and (data["schema_version"] != 2 or not isinstance(child["kinds"], list)
                                 or len(child["kinds"]) != len(child["lines"])
                                 or any(kind not in KINDS for kind in child["kinds"])):
            raise ValueError("line kinds must match the lines one to one")

    def children(value, limit):
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("exactly two children are required")
        for child in value:
            obj(child, {"name", "heading", "lines", "kinds"}, {"name", "heading", "lines"})
            line(child["name"], 24, "child.name")
            line(child["heading"], 48, "child.heading")
            if not isinstance(child["lines"], list) or len(child["lines"]) > limit:
                raise ValueError("too many child lines")
            for text in child["lines"]:
                line(text, 100, "child.lines[]")
            kinds(child)
        if value[0]["name"] == value[1]["name"]:
            raise ValueError("children must be distinct")

    obj(data, {"schema_version", "week_label", "updated_label", "hero", "weekend", "past_summary",
               "horizon", "later", "weekdays", "horizon_label", "footer", "messages", "freshness",
               "weather", "day_weather", "weekend_overflow", "day_summaries", "review_count",
               "day_view", "ahead", "later_events", "device"},
              {"schema_version", "week_label", "updated_label", "hero", "messages"})
    if type(data["schema_version"]) is not int or data["schema_version"] not in {1, 2}:
        raise ValueError("schema_version must be 1 or 2")
    for key in ("week_label", "updated_label", "past_summary", "horizon_label", "footer"):
        line(data.get(key, ""), 160, key)
    obj(data["hero"], {"state", "day", "children"}, {"state", "day", "children"})
    line(data["hero"]["state"], 30, "hero.state")
    line(data["hero"]["day"], 30, "hero.day")
    children(data["hero"]["children"], 5 if data["schema_version"] == 2 else 3)
    weekend = data.get("weekend", [])
    if not isinstance(weekend, list) or len(weekend) > 2:
        raise ValueError("at most two weekend activities")
    for item in weekend:
        obj(item, {"day", "text"}, {"day", "text"})
        line(item["day"], 16, "weekend[].day")
        line(item["text"], 100, "weekend[].text")
        if len(item["text"]) > 48:
            raise ValueError("weekend activity text must fit display")
    overflow = data.get("weekend_overflow", 0)
    if type(overflow) is not int or not 0 <= overflow <= 9:
        raise ValueError("weekend_overflow must be a count from 0 to 9")
    review = data.get("review_count", 0)
    if type(review) is not int or not 0 <= review <= 99:
        raise ValueError("review_count must be a count from 0 to 99")
    names = [c["name"] for c in data["hero"]["children"]]
    if data["schema_version"] == 1:
        if "horizon" not in data or not isinstance(data["horizon"], list) or len(data["horizon"]) > 3:
            raise ValueError("at most three horizon days")
        for day in data["horizon"]:
            obj(day, {"day", "children", "shared"}, {"day", "children"})
            line(day["day"], 16, "horizon[].day")
            children(day["children"], 2)
            if [c["name"] for c in day["children"]] != names:
                raise ValueError("child order must match the hero")
            if day.get("shared") is not None:
                line(day["shared"], 100, "horizon[].shared")
        later = data.get("later", [])
        if not isinstance(later, list) or len(later) > 2:
            raise ValueError("at most two folded lookahead days")
        if "later" in data and (len(data["horizon"]) != 3 or len(later) != 2):
            raise ValueError("five-day lookahead requires three unfolded and two folded days")
        for item in later:
            obj(item, {"day", "summary"}, {"day", "summary"})
            line(item["day"], 16, "later[].day")
            line(item["summary"], 100, "later[].summary")
    else:
        if "horizon" in data or "later" in data:
            raise ValueError("schema version 2 uses weekdays instead of horizon and later")
        if data.get("footer"):
            raise ValueError("schema version 2 reserves the fixed weekend area")
        weekdays = data.get("weekdays")
        if not isinstance(weekdays, list) or len(weekdays) != 5:
            raise ValueError("schema version 2 requires five weekdays")
        for day in weekdays:
            obj(day, {"date", "day", "children", "shared", "summary", "shared_kind"},
                {"date", "day", "children", "shared", "summary"})
            if "shared_kind" in day:
                from skoleklar.parents_board import KINDS
                if day["shared"] is None or day["shared_kind"] not in KINDS:
                    raise ValueError("a shared kind needs a shared line and a known kind")
            try:
                weekday_date = datetime.fromisoformat(day["date"]).date()
            except (TypeError, ValueError) as error:
                raise ValueError("weekday date must be ISO format") from error
            if weekday_date.weekday() >= 5:
                raise ValueError("weekday entries must be Monday through Friday")
            line(day["day"], 16, "weekdays[].day")
            line(day["summary"], 100, "weekdays[].summary")
            children(day["children"], 3)
            if [c["name"] for c in day["children"]] != names:
                raise ValueError("child order must match the hero")
            if day["shared"] is not None:
                line(day["shared"], 100, "weekdays[].shared")
    if {"ahead", "later_events", "device"} & set(data):
        from skoleklar.parents_board import validate_parents_board
        validate_parents_board(data, names)
    if "day_weather" in data:
        days = data["day_weather"]
        if data["schema_version"] != 2 or not isinstance(days, list) or len(days) > 5:
            raise ValueError("day weather is at most five school days in schema version 2")
        for day in days:
            obj(day, {"date", "icon", "low", "high", "rain_mm"}, {"date", "icon", "low", "high", "rain_mm"})
            try:
                date.fromisoformat(day["date"])
            except (TypeError, ValueError) as error:
                raise ValueError("day weather date must be ISO format") from error
            integer(day["rain_mm"])
            if day["icon"] not in CELL_ICONS or any(
                    type(day[key]) is not int or not -60 <= day[key] <= 60 for key in ("low", "high")):
                raise ValueError("invalid day weather")
    if "day_summaries" in data:
        # The weekly plan retold per day for the child; attached after composition.
        days = data["day_summaries"]
        weekdays = {day["date"] for day in data.get("weekdays", [])}
        if data["schema_version"] != 2 or not isinstance(days, list) or len(days) > 5:
            raise ValueError("day summaries are at most five school days in schema version 2")
        for day in days:
            obj(day, {"date", "children"}, {"date", "children"})
            if day["date"] not in weekdays:
                raise ValueError("day summary date must be a displayed weekday")
            if not isinstance(day["children"], list) or [c.get("name") if isinstance(c, dict) else None
                                                         for c in day["children"]] != names:
                raise ValueError("day summary children must match the hero")
            for child in day["children"]:
                obj(child, {"name", "status", "headline", "text"}, {"name", "status", "headline", "text"})
                if child["status"] not in {"ok", "no_content", "not_published", "unavailable"}:
                    raise ValueError("unknown day summary status")
                if child["status"] == "ok":
                    line(child["headline"], 48, "day_summaries[].headline")
                    line(child["text"], 480, "day_summaries[].text")
                elif child["headline"] is not None or child["text"] is not None:
                    raise ValueError("only an ok day summary carries text")
    if "weather" in data:
        if data["schema_version"] != 2:
            raise ValueError("weather requires schema version 2")
        weather = data["weather"]
        obj(weather, {"place", "verdict", "reason", "label", "days", "note"},
            {"place", "verdict", "reason", "label", "days"})
        line(weather["place"], 24, "weather.place")
        line(weather["reason"], 90, "weather.reason")
        line(weather["label"], 24, "weather.label")
        line(weather.get("note", ""), 48, "weather.note")
        if weather["verdict"] not in VERDICT_ICONS:
            raise ValueError("unknown weather verdict")
        if not isinstance(weather["days"], list) or not 1 <= len(weather["days"]) <= 2:
            raise ValueError("weather shows one or two days")
        hours = WEATHER_LAST_HOUR - WEATHER_FIRST_HOUR
        for position, day in enumerate(weather["days"]):
            keys = {"date", "day", "sunrise", "sunset", "icon", "low", "high",
                    "cells", "flags", "hours", "window"}
            obj(day, keys | {"rain_mm"}, keys)
            if "rain_mm" in day:
                integer(day["rain_mm"])
            if day["icon"] not in CELL_ICONS or any(
                    type(day[key]) is not int or not -60 <= day[key] <= 60 for key in ("low", "high")):
                raise ValueError("invalid weather day summary")
            try:
                weekend_day = date.fromisoformat(day["date"])
            except (TypeError, ValueError) as error:
                raise ValueError("weather date must be ISO format") from error
            # The first day is always a weekend day; Monday may only follow Sunday.
            if weekend_day.weekday() < 5 and not (position == 1 and weekend_day.weekday() == 0):
                raise ValueError("weather days must be Saturday, Sunday or the Monday after")
            line(day["day"], 16, "weather.days[].day")
            for key in ("sunrise", "sunset"):
                if not isinstance(day[key], str) or not re.fullmatch(r"[0-2]\d:[0-5]\d", day[key]):
                    raise ValueError("sunrise and sunset must be HH:MM")
            if not isinstance(day["hours"], str) or not re.fullmatch(rf"[scr]{{{hours}}}", day["hours"]):
                raise ValueError("weather hours must be one of s, c, r per display hour")
            if not isinstance(day["cells"], list) or len(day["cells"]) != 2:
                raise ValueError("weather days have two half-day cells")
            for cell in day["cells"]:
                obj(cell, {"label", "icon", "temp", "rain_mm"}, {"label", "icon", "temp", "rain_mm"})
                line(cell["label"], 12, "weather.cells[].label")
                integer(cell["rain_mm"])
                if cell["icon"] not in CELL_ICONS or type(cell["temp"]) is not int or abs(cell["temp"]) > 60:
                    raise ValueError("invalid weather cell")
            if not isinstance(day["flags"], list) or len(day["flags"]) > 2:
                raise ValueError("at most two weather flags per day")
            for flag in day["flags"]:
                obj(flag, {"icon", "label"}, {"icon", "label"})
                line(flag["label"], 10, "weather.flags[].label")
                if flag["icon"] not in FLAG_ICONS:
                    raise ValueError("unknown weather flag")
            window = day["window"]
            if window is not None:
                obj(window, {"start", "end", "label"}, {"start", "end", "label"})
                line(window["label"], 24, "weather.window.label")
                if (type(window["start"]) is not int or type(window["end"]) is not int
                        or not WEATHER_FIRST_HOUR <= window["start"] < window["end"] <= WEATHER_LAST_HOUR):
                    raise ValueError("weather window must lie inside the display hours")
    messages = data["messages"]
    obj(messages, {"checked", "bodies_retained", "unread_count", "new_count", "items"},
                  {"checked", "bodies_retained", "unread_count", "items"})
    integer(messages.get("new_count", 0))
    if type(messages["checked"]) is not bool or type(messages["bodies_retained"]) is not int or messages["bodies_retained"] != 0:
        raise ValueError("checked must be boolean; bodies_retained must be zero")
    integer(messages["unread_count"])
    if not isinstance(messages["items"], list) or len(messages["items"]) > 2:
        raise ValueError("at most two message rows")
    for item in messages["items"]:
        obj(item, {"participant", "children", "activity", "unread", "new_thread", "new_message",
                   "latest_unretrieved", "new"},
                  {"participant", "children", "activity", "unread"})
        if not valid_participant_label(item["participant"]):
            raise ValueError("participant must be shortened to First I.")
        if not isinstance(item["children"], list) or not item["children"] or any(c not in names for c in item["children"]):
            raise ValueError("unknown message child")
        line(item["activity"], 30, "messages.items[].activity")
        for key in ("unread", "new_thread", "new_message", "latest_unretrieved", "new"):
            if key in item and type(item[key]) is not bool:
                raise ValueError("message flags must be boolean")
    if "freshness" in data:
        fresh = data["freshness"]
        obj(fresh, {"source_updated_at", "composed_at", "expires_at", "status"},
                   {"source_updated_at", "composed_at", "expires_at", "status"})
        if fresh["status"] not in {"fresh", "stale", "partial", "unknown"}:
            raise ValueError("invalid freshness status")
        for key in ("source_updated_at", "composed_at", "expires_at"):
            timestamp(fresh[key])
        if timestamp(fresh["expires_at"]) <= timestamp(fresh["source_updated_at"]):
            raise ValueError("expiry must follow source update")
    if "day_view" in data:
        from skoleklar.day_view_render import validate_day_view
        validate_day_view(data)


def fit(draw: ImageDraw.ImageDraw, value: str, width: int, selected: ImageFont.ImageFont) -> str:
    if draw.textbbox((0, 0), value, font=selected)[2] <= width:
        return value
    result = value
    while result and draw.textbbox((0, 0), result + "…", font=selected)[2] > width:
        result = result[:-1]
    return result.rstrip() + "…"


def _draw_protected_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
                         selected: ImageFont.ImageFont, colour: str) -> None:
    """Draw text and mark the same glyphs as hard-quantized panel pixels."""
    draw.text(xy, value, font=selected, fill=colour)
    mask_draw = getattr(draw, "_ag_text_mask", None)
    if mask_draw is not None:
        mask_draw.text(xy, value, font=selected, fill=255)


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, style: str,
              colour: str = BLACK, width: int | None = None) -> None:
    selected, shift = styled_font(style)
    if width is not None:
        value = fit(draw, value, width, selected)
    _draw_protected_text(draw, (xy[0], xy[1] + shift), value, selected, colour)


def draw_marked_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, style: str,
                     width: int | None = None) -> None:
    """Attention without red: ink text on a yellow marker."""
    selected, shift = styled_font(style)
    if width is not None:
        value = fit(draw, value, width - 6, selected)
    box = draw.textbbox((xy[0] + 3, xy[1] + shift), value, font=selected)
    draw.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill=YELLOW)
    _draw_protected_text(draw, (xy[0] + 3, xy[1] + shift), value, selected, BLACK)


def draw_adaptive_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
                       styles: tuple[str, ...], colour: str, width: int) -> None:
    """Use the first legible style that fits, then truncate with the smallest."""
    for style in styles:
        selected, shift = styled_font(style)
        if draw.textbbox((0, 0), value, font=selected)[2] <= width:
            _draw_protected_text(draw, (xy[0], xy[1] + shift), value, selected, colour)
            return
    draw_text(draw, xy, value, styles[-1], colour, width)


def wrapped_lines(draw: ImageDraw.ImageDraw, value: str, width: int,
                  style: str, limit: int = 2) -> list[str]:
    selected = styled_font(style)[0]
    words = str(value).split()
    result = []
    while words and len(result) < limit:
        line = words.pop(0)
        while words and draw.textbbox((0, 0), line + " " + words[0], font=selected)[2] <= width:
            line += " " + words.pop(0)
        result.append(line)
    if words:
        result[-1] = fit(draw, result[-1] + " " + " ".join(words), width, selected)
    return result


# A wrapped line of one item is indented by this much, so it reads as the same item
# and not as a new one (the HTML uses a hanging indent for the same reason).
HANG = 8
# The extra space between two items in the hero, used only while everything fits.
ITEM_GAP = 4


def hanging_lines(draw: ImageDraw.ImageDraw, value: str, width: int, style: str,
                  limit: int = 2, *, first: int = 0) -> list[tuple[int, str]]:
    """Wrap one item as (x offset, text) rows: the first at `first`, the rest at HANG."""
    selected = styled_font(style)[0]
    words = str(value).split()
    result: list[tuple[int, str]] = []
    while words and len(result) < limit:
        offset = HANG if result else first
        line = words.pop(0)
        while words and draw.textbbox((0, 0), line + " " + words[0], font=selected)[2] <= width - offset:
            line += " " + words.pop(0)
        result.append((offset, line))
    if words:
        offset, line = result[-1]
        result[-1] = (offset, fit(draw, line + " " + " ".join(words), width - offset, selected))
    return result


def draw_hero_detail(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
                     colour: str, width: int, style: str = "small") -> None:
    """Keep dense hero facts complete by stepping down before truncating."""
    styles = ("small", "micro", "nano")
    for style in styles[styles.index(style):]:
        selected = styled_font(style)[0]
        if draw.textbbox((0, 0), value, font=selected)[2] <= width:
            _draw_protected_text(draw, xy, value, selected, colour)
            return
    draw_text(draw, xy, value, "nano", colour, width)


def day_weather(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The optional per-school-day weather, keyed by ISO date."""
    return {item["date"]: item for item in data.get("day_weather", [])}


def focus_date(data: dict[str, Any]) -> str | None:
    """The school date the hero details, as the clock policy sees it at composition."""
    composed = data.get("freshness", {}).get("composed_at")
    return dashboard_focus(timestamp(composed)).school_date.isoformat() if composed else None


def temperature_range(item: dict[str, Any]) -> str:
    low, high = item["low"], item["high"]
    return f"{high}°" if low == high else f"{low}–{high}°"


def new_suffix(messages: dict[str, Any]) -> str:
    """' (1 NY)' while messages are new: the last 24 hours, or since the weekend began."""
    count = messages.get("new_count", 0)
    return f" ({count} NY)" if count else ""


def new_arrivals(data: dict[str, Any]) -> list[str]:
    """'Child · Sender' for each inbox row that counts as new."""
    messages = data.get("messages", {})
    if not messages.get("checked") or is_stale(data):
        return []
    return [f'{item["children"][0]} · {item["participant"]}'
            for item in messages.get("items", []) if item.get("new")]


def hero_visual_items(lines: list[str]) -> list[list[str]]:
    """The hero's items, each as the segments that start a new row.

    A substitute is one item of two segments, teacher then class, so both stay
    visible without shrinking below panel legibility.
    """
    result: list[list[str]] = []
    for original in lines:
        line = short_substitute(original)
        if str(line).casefold().startswith("vikar ") and "·" in line:
            person, scope = line.rsplit("·", 1)
            result.append([person.strip(), scope.strip()])
        elif (result and len(result[-1]) == 1 and str(line).startswith("Medbring:")
              and str(result[-1][0]).startswith("Medbring:")):
            # One bring list wraps tighter than a repeated label per item.
            result[-1][0] += "; " + str(line)[len("Medbring:"):].strip()
        else:
            result.append([line])
    return result


def hero_visual_lines(lines: list[str]) -> list[str]:
    """The hero's segments in order, one per forced row."""
    return [segment for item in hero_visual_items(lines) for segment in item]


def display_heading(child: dict[str, Any]) -> str:
    """Hide malformed headings that only repeat the already-visible child name."""
    heading = str(child.get("heading", "")).strip()
    name = str(child.get("name", "")).strip()
    return "" if heading.casefold() == name.casefold() else heading


def _html_text_width(value: str) -> float:
    """Conservatively approximate 12 px Georgia text without a browser or Pillow."""
    narrow = " .,:;!'|ijlIrtf()[]"
    wide = "MWmw@%ÆØÅæøå"
    return sum(3.2 if char in narrow else 9.0 if char in wide else
               7.2 if char.isupper() else 6.0 for char in value)


def _html_line_fragments(value: str, *, width: int = 148) -> list[str]:
    """Wrap a dashboard line before HTML layout so its height is deterministic."""
    words = str(value).split()
    if not words:
        return [""]
    result: list[str] = []
    current = ""
    for word in words:
        # Long tokens are rare in the dashboard, but splitting them prevents a
        # single token from silently overflowing a child column.
        chunks: list[str] = []
        chunk = ""
        for char in word:
            if chunk and _html_text_width(chunk + char) > width:
                chunks.append(chunk)
                chunk = char
            else:
                chunk += char
        if chunk:
            chunks.append(chunk)
        for chunk in chunks:
            candidate = f"{current} {chunk}".strip()
            if current and _html_text_width(candidate) > width:
                result.append(current)
                current = chunk
            else:
                current = candidate
    if current:
        result.append(current)
    return result


def _weekday_child_rows(child: dict[str, Any]) -> int:
    return ((1 if display_heading(child) else 0)
            + sum(len(_html_line_fragments(line)) for line in child.get("lines", [])))


PINNED_WEEKDAYS = 2
# Headings that ask the reader to decide or prepare something.
PREPARE_HEADINGS = frozenset({"Valgfrit", "Afklar"})


def _first_relevant_date(data: dict[str, Any]) -> date:
    """First school date selected by the shared Copenhagen timing policy."""
    composed = timestamp(data["freshness"]["composed_at"])
    return dashboard_focus(composed).school_date


def pinned_weekdays(data: dict[str, Any]) -> set[int]:
    """Indexes of the next two school days; these are never folded."""
    days = data.get("weekdays", [])
    start = _first_relevant_date(data)
    dates = [date.fromisoformat(day["date"]) for day in days]
    upcoming = [index for index, value in enumerate(dates) if value >= start]
    pinned = upcoming[:PINNED_WEEKDAYS]
    if len(pinned) < PINNED_WEEKDAYS:
        # Late in the week: top up with the nearest remaining days.
        rest = sorted((i for i in range(len(days)) if i not in pinned),
                      key=lambda i: (abs((dates[i] - start).days), -dates[i].toordinal()))
        pinned += rest[: PINNED_WEEKDAYS - len(pinned)]
    return set(pinned)


def _fold_weekday_layout(data: dict[str, Any], heights: list[int],
                         available_height: int, *, strict: bool = False) -> list[dict[str, Any]]:
    """Apply the shared temporal folding policy to measured row heights.

    `strict` is for the fixed panel, where rows that do not fit would overlap
    the weekend section; the browser folds any remaining overflow itself.
    """
    days = data.get("weekdays", [])
    if not days:
        return []
    folded: set[int] = set()
    pinned = pinned_weekdays(data)
    # Past days fold first (oldest first), then the future days farthest away.
    start = _first_relevant_date(data)
    candidates = sorted(
        (index for index in range(len(days)) if index not in pinned),
        key=lambda index: (
            date.fromisoformat(days[index]["date"]) < start,
            abs((date.fromisoformat(days[index]["date"]) - start).days),
            -date.fromisoformat(days[index]["date"]).toordinal(),
        ),
        reverse=True,
    )
    # A day that is already over never needs its detail: fold it regardless of room.
    past = {index for index in candidates if date.fromisoformat(days[index]["date"]) < start}
    folded |= past
    def fits():
        return sum(25 if i in folded else height for i, height in enumerate(heights)) <= available_height

    for index in candidates:
        if fits():
            break
        if len(folded) >= min(3, len(candidates)) and index not in past:
            break
        folded.add(index)
    if strict and not fits():
        # The hero already shows its own day in full, so that row folds first;
        # then any day, farthest first. Freed room returns to the nearest days.
        hero = start.isoformat()
        overflow = sorted(range(len(days)), key=lambda index: (
            days[index]["date"] != hero,
            -abs((date.fromisoformat(days[index]["date"]) - start).days)))
        for index in overflow:
            if fits():
                break
            folded.add(index)
        for index in reversed(candidates):
            if index in folded and index not in past and days[index]["date"] != hero:
                folded.discard(index)
                if not fits():
                    folded.add(index)
    result = [
        {"day": day, "folded": index in folded, "pinned": index in pinned,
         "rank": candidates.index(index) if index in candidates else None,
         "past": index in past,
         "height": 25 if index in folded else heights[index]}
        for index, day in enumerate(days)
    ]
    spare = max(0, available_height - sum(item["height"] for item in result))
    expandable = [item for item in result if not item["folded"]]
    if expandable:
        addition, remainder = divmod(spare, len(expandable))
        for index, item in enumerate(expandable):
            item["height"] += addition + (1 if index < remainder else 0)
    return result


def weekday_layout(data: dict[str, Any], *, available_height: int = 309) -> list[dict[str, Any]]:
    """Choose HTML folds by density, starting with dates farthest from today."""
    days = data.get("weekdays", [])

    def expanded_height(day):
        child_rows = [max(1, _weekday_child_rows(child)) for child in day["children"]]
        shared_rows = len(_html_line_fragments(day["shared"], width=314)) if day.get("shared") else 0
        # HTML uses a fixed 15 px line band and 10 px vertical padding. Count
        # wrapped visual lines here as well as logical state lines. Never cap an
        # expanded row: a row that does not fit must be folded, not clipped.
        return max(42, 10 + max(child_rows or [1]) * 15 + shared_rows * 15)

    return _fold_weekday_layout(
        data, [expanded_height(day) for day in days], available_height
    )


def _eink_child_lines(draw: ImageDraw.ImageDraw, child: dict[str, Any],
                      width: int = 153) -> list[tuple[int, str]]:
    """A week-table cell as (x offset, text) rows, wrapped lines hanging."""
    result = []
    for line in display_lines(child):
        result.extend(hanging_lines(draw, line, width, "micro", limit=2))
    return result


def eink_weekday_layout(data: dict[str, Any], draw: ImageDraw.ImageDraw,
                        *, available_height: int = 309) -> list[dict[str, Any]]:
    """Measure 14 px panel text and fold rows before any detail is clipped."""
    heights = []
    for day in data.get("weekdays", []):
        child_rows = [
            (1 if display_heading(child) else 0) + len(_eink_child_lines(draw, child))
            for child in day["children"]
        ]
        shared_rows = (len(wrapped_lines(draw, day["shared"], 324, "micro_b", limit=2))
                       if day.get("shared") else 0)
        heights.append(max(48, 10 + max(child_rows or [1]) * 17 + shared_rows * 17))
    return _fold_weekday_layout(data, heights, available_height, strict=True)


def weekend_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    """Turn compact weekend facts into day + two child columns or one shared row."""
    names = [child["name"] for child in data["hero"]["children"]]
    result = []
    for item in data.get("weekend", []):
        text = str(item["text"]).strip()
        row = {"day": str(item["day"]), names[0]: "", names[1]: "", "shared": ""}
        for name in names:
            prefix = name + ":"
            if text.casefold().startswith(prefix.casefold()):
                row[name] = text[len(prefix):].strip()
                break
        else:
            row["shared"] = text
        result.append(row)
    return result


def weekend_row_parts(row: dict[str, str], names: list[str]) -> tuple[str, str]:
    """('Beta:', 'Fest kl. 14') for a weekend row: whose it is, then the activity.

    A row spans both child columns, so the prefix says whose it is; a shared row
    keeps its own 'Begge:' and has no prefix when it names nobody.
    """
    for name in names:
        if row.get(name):
            return f"{name}:", row[name]
    head, separator, rest = row["shared"].partition(":")
    if separator and head.strip().casefold() == "begge":
        return f"{head.strip()}:", rest.strip()
    return "", row["shared"]


def panel_palette() -> list[int]:
    """Return a 256-entry PNG palette with colours at their hardware codes."""
    palette = [0] * 768
    for code, colour in PANEL_NOMINAL_RGB.items():
        palette[code * 3:code * 3 + 3] = colour
    return palette


def quantize_for_panel(image: Image.Image, *, mode: str = "hybrid",
                       protected_mask: Image.Image | None = None) -> Image.Image:
    """Convert RGB to exact Spectra 6 codes with optional error diffusion.

    ``crisp`` performs the original hard mapping, ``full`` applies serpentine
    Floyd-Steinberg throughout, and ``hybrid`` hard-maps protected text while
    dithering other surfaces. Each dashboard panel is diffused independently,
    so a large dark field cannot push accumulated error into the light panel.
    """
    from PIL import Image

    if mode not in DITHER_MODES:
        raise ValueError(f"Unknown dither mode: {mode}")
    source = image.convert("RGB")
    if protected_mask is not None and protected_mask.size != source.size:
        raise ValueError("Protected mask must match the panel image")

    source_pixels = list(source.get_flattened_data())
    mask_pixels = (bytes(protected_mask.convert("L").get_flattened_data())
                   if protected_mask is not None else None)
    hard_cache: dict[tuple[int, int, int], int] = {}

    def hard_nearest(pixel: tuple[int, int, int]) -> int:
        if pixel not in hard_cache:
            # Neutral antialiasing must remain achromatic. Otherwise glyph
            # edges acquire coloured fringes and look soft on the real panel.
            if max(pixel) - min(pixel) <= 32:
                luminance = (2126 * pixel[0] + 7152 * pixel[1] + 722 * pixel[2]) // 10000
                hard_cache[pixel] = 0x1 if luminance >= 129 else 0x0
            else:
                anchor = min(
                    DESIGN_PANEL_CODES,
                    key=lambda colour: sum(
                        (pixel[channel] - colour[channel]) ** 2
                        for channel in range(3)
                    ),
                )
                hard_cache[pixel] = DESIGN_PANEL_CODES[anchor]
        return hard_cache[pixel]

    if mode == "crisp":
        codes = bytes(hard_nearest(pixel) for pixel in source_pixels)
    else:
        # Diffuse in linear-light space. This avoids the overly dark, dirty
        # mixtures produced by doing the arithmetic directly in sRGB.
        linear = tuple((value / 255.0) ** 2.2 for value in range(256))
        palette_linear = {
            code: tuple(linear[channel] for channel in colour)
            for code, colour in PANEL_APPEARANCE_RGB.items()
        }
        all_codes = PANEL_DESIGN_CODES
        neutral_codes = (0x0, 0x1)
        output = bytearray(source.width * source.height)

        def nearest_linear(value: tuple[float, float, float], *, neutral: bool) -> int:
            candidates = neutral_codes if neutral else all_codes
            return min(candidates, key=lambda code: sum(
                (value[channel] - palette_linear[code][channel]) ** 2
                for channel in range(3)
            ))

        boundaries = (0, PANEL_SPLIT_X, source.width)
        for left, right in zip(boundaries, boundaries[1:]):
            segment_width = right - left
            current = [[0.0] * (segment_width + 2) for _ in range(3)]
            for y in range(source.height):
                following = [[0.0] * (segment_width + 2) for _ in range(3)]
                reverse = bool(y % 2)
                positions = range(segment_width - 1, -1, -1) if reverse else range(segment_width)
                for local_x in positions:
                    x = left + local_x
                    index = y * source.width + x
                    original = source_pixels[index]
                    protected = bool(mask_pixels and mask_pixels[index]) if mode == "hybrid" else False
                    if protected:
                        output[index] = hard_nearest(original)
                        continue
                    adjusted = tuple(max(0.0, min(1.0,
                        linear[original[channel]] + current[channel][local_x + 1]
                    )) for channel in range(3))
                    neutral = max(original) - min(original) <= 32
                    code = nearest_linear(adjusted, neutral=neutral)
                    output[index] = code
                    error = tuple((adjusted[channel] - palette_linear[code][channel]) * DITHER_STRENGTH
                                  for channel in range(3))
                    for channel in range(3):
                        if reverse:
                            current[channel][local_x] += error[channel] * 7 / 16
                            following[channel][local_x + 2] += error[channel] * 3 / 16
                            following[channel][local_x + 1] += error[channel] * 5 / 16
                            following[channel][local_x] += error[channel] * 1 / 16
                        else:
                            current[channel][local_x + 2] += error[channel] * 7 / 16
                            following[channel][local_x] += error[channel] * 3 / 16
                            following[channel][local_x + 1] += error[channel] * 5 / 16
                            following[channel][local_x + 2] += error[channel] * 1 / 16
                current = following
        codes = bytes(output)

    result = Image.frombytes("P", source.size, codes)
    result.putpalette(panel_palette())
    return result


def validate_panel_image(image: Image.Image) -> None:
    """Reject images that a Spectra 6 driver cannot send byte-for-byte."""
    if image.size != (WIDTH, HEIGHT) or image.mode != "P":
        raise ValueError("Invalid panel image dimensions or mode")
    used = {index for _count, index in (image.getcolors(maxcolors=256) or [])}
    if not used <= set(PANEL_NOMINAL_RGB):
        raise ValueError("Panel image contains an unsupported pixel code")
    palette = image.getpalette()
    if palette is None or any(
        tuple(palette[code * 3:code * 3 + 3]) != colour
        for code, colour in PANEL_NOMINAL_RGB.items()
    ):
        raise ValueError("Panel image palette does not match Spectra 6")


def render_panel_preview(image: Image.Image) -> Image.Image:
    """Replace nominal panel colours with a monitor approximation."""
    from PIL import Image

    validate_panel_image(image)
    pixels = [PANEL_APPEARANCE_RGB[index] for index in image.get_flattened_data()]
    result = Image.new("RGB", image.size)
    result.putdata(pixels)
    return result


def pack_panel_frame(image: Image.Image) -> bytes:
    """Pack two hardware colour codes per byte, left pixel in the high nibble."""
    validate_panel_image(image)
    pixels = bytes(image.get_flattened_data())
    if len(pixels) % 2:
        raise ValueError("Panel rows must contain an even number of pixels")
    return bytes((pixels[index] << 4) | pixels[index + 1]
                 for index in range(0, len(pixels), 2))


def inbox_height(draw: ImageDraw.ImageDraw, data: dict[str, Any], live_messages=None) -> int:
    """Height the hero panel's inbox block draws, from its rule to its last row."""
    if live_messages is not None:
        subjects = [len(wrapped_lines(draw, item.text, 126, "micro"))
                    for item in live_messages[:2] if item.status != "redacted"]
        return 91 + 16 * max(subjects, default=0) + 4
    if data.get("messages", {}).get("checked") and not is_stale(data):
        return 97
    return 40


def draw_inbox_child(draw: ImageDraw.ImageDraw, xy: tuple[int, int], name: str,
                     status: str, width: int = 126) -> None:
    """A child's name with its message status right after it, the status kept whole."""
    status_font = styled_font("micro_b")[0]
    room = width - (int(draw.textlength(status, font=status_font)) + 8 if status else 0)
    name_font = styled_font("body_b")[0]
    shown = fit(draw, name, room, name_font)
    draw_text(draw, xy, shown, "body_b", WHITE)
    if status:
        # Two pixels down puts the 14 px status on the 16 px name's baseline.
        draw_text(draw, (xy[0] + int(draw.textlength(shown, font=name_font)) + 8, xy[1] + 2),
                  status, "micro_b", WHITE)


def draw_hero_panel(draw: ImageDraw.ImageDraw, data: dict[str, Any], live_messages=None) -> None:
    hero = data["hero"]
    draw_text(draw, (28, 22), hero["state"], "small_b", WHITE)
    today = day_weather(data).get(focus_date(data))
    if today:
        label = temperature_range(today)
        width = int(draw.textlength(label, font=styled_font("body_b")[0]))
        draw_icon(draw, today["icon"], 312 - width - 36, 10, 30, HERO_ICON_COLOURS)
        draw_text(draw, (312 - width, 20), label, "body_b", WHITE)
    draw_text(draw, (28, 41), hero["day"], "hero", WHITE, 284)
    # Dense hero detail may push the inbox down as far as its own rows allow
    # before anything is shortened.
    detail_limit = HEIGHT - inbox_height(draw, data, live_messages) - 8
    hero_columns = ((28, 140), (178, 134))
    detail_bottom = 0
    for index, child in enumerate(hero["children"]):
        x, column_width = hero_columns[index]
        draw_text(draw, (x, 101), child["name"], "name", WHITE, column_width)
        heading = display_heading(child)
        heading_lines = []
        if heading:
            heading_lines = wrapped_lines(draw, heading, column_width, "micro_b", limit=2)
            for row, line in enumerate(heading_lines):
                draw_text(draw, (x, 134 + row * 17), line, "micro_b", WHITE, column_width)
        line_top = 134 + len(heading_lines) * 17 + (4 if heading_lines else 0)
        # Step down to the 14 px face before shortening any line.
        for style, pitch in (("small", 18), ("micro", 15)):
            items = [
                [row for part, segment in enumerate(item)
                 for row in hanging_lines(draw, segment, column_width, style, limit=12,
                                          first=HANG if part else 0)]
                for item in hero_visual_items(child.get("lines", []))
            ]
            rows = sum(len(item) for item in items)
            if line_top + rows * pitch <= detail_limit:
                break
        # Space between items only while every row still fits.
        gap = ITEM_GAP if line_top + rows * pitch + max(0, len(items) - 1) * ITEM_GAP <= detail_limit else 0
        placed, y = [], line_top
        for item in items:
            for offset, line in item:
                placed.append((y, offset, line))
                y += pitch
            y += gap
        visible = [row for row in placed if row[0] + pitch <= detail_limit]
        if visible and len(visible) < len(placed):
            top, offset, line = visible[-1]
            visible[-1] = (top, offset, fit(draw, line + "…", column_width - offset,
                                            styled_font(style)[0]))
        for top, offset, line in visible:
            draw_hero_detail(draw, (x + offset, top), line, WHITE, column_width - offset, style)
        detail_bottom = max(detail_bottom, visible[-1][0] + pitch if visible else line_top)

    weekend = data.get("weekend", [])
    if weekend and data["schema_version"] == 1:
        draw_text(draw, (28, 224), "WEEKEND", "micro_b", WHITE)
        if len(weekend) == 1:
            draw_text(draw, (28, 242), weekend[0]["day"], "micro_b", WHITE, 284)
            draw_text(draw, (28, 258), weekend[0]["text"], "small", WHITE, 284)
        else:
            for row, item in enumerate(weekend):
                y = 242 + row * 30
                draw_text(draw, (28, y), item["day"], "micro_b", WHITE, 284)
                draw_text(draw, (28, y + 14), item["text"], "small", WHITE, 284)
    messages = data.get("messages", {})
    message_top = max(304, detail_bottom + 8) if data["schema_version"] == 2 else (
        306 if len(weekend) == 2 else (282 if weekend else 278)
    )
    draw.line((28, message_top, 312, message_top), fill=WHITE, width=1)
    if live_messages is not None:
        unread = int(messages.get("unread_count", sum(bool(item.unread) for item in live_messages)))
        draw_text(draw, (28, message_top + 12), f"BESKEDER · {unread} ULÆSTE{new_suffix(messages)}", "small_b", WHITE)
        by_child = {item.child: item for item in live_messages[:2]}
        for index, child in enumerate(hero["children"]):
            item = by_child.get(child["name"])
            if item is None and index < len(live_messages):
                item = live_messages[index]
            if item is None:
                continue
            x = 28 + index * 158
            draw_inbox_child(draw, (x, message_top + 38), child["name"],
                             "ULÆST" if item.unread else "")
            draw_text(draw, (x, message_top + 57), item.activity, "micro", WHITE, 126)
            sender = item.sender + (" (redacted)" if item.status == "redacted" else "")
            draw_text(draw, (x, message_top + 73), sender, "small_b", WHITE, 126)
            if item.status != "redacted":
                for row, subject_line in enumerate(wrapped_lines(draw, item.text, 126, "micro")):
                    draw_text(draw, (x, message_top + 91 + row * 16), subject_line,
                              "micro", WHITE, 126)
    elif messages.get("checked") and not is_stale(data):
        unread = int(messages.get("unread_count", sum(bool(item.get("unread")) for item in messages.get("items", []))))
        draw_text(draw, (28, message_top + 12), f"BESKEDER · {unread} ULÆSTE{new_suffix(messages)}", "small_b", WHITE)
        for index, child in enumerate(hero["children"]):
            item = next((row for row in messages.get("items", []) if child["name"] in row.get("children", [])), None)
            if item is None:
                continue
            x = 28 + index * 158
            status = "NY" if item.get("new") else "ULÆST" if item.get("unread") else "SET"
            draw_inbox_child(draw, (x, message_top + 38), child["name"], status)
            draw_text(draw, (x, message_top + 57), item.get("activity", ""), "micro", WHITE, 126)
            draw_text(draw, (x, message_top + 73), item["participant"], "small_b", WHITE, 126)
    else:
        draw_text(draw, (28, message_top + 18), "BESKEDER IKKE TJEKKET", "small_b", WHITE)


def review_label(data: dict[str, Any]) -> str:
    """'2 punkter at tjekke' for this week's uncertain facts the board leaves out."""
    count = data.get("review_count", 0)
    return f"{count} {'punkt' if count == 1 else 'punkter'} at tjekke" if count else ""


def corner_freshness(data: dict[str, Any]) -> str:
    """'Opdateret 31/10 05.45', prefixed when the data is old or partial."""
    prefix = "ÆLDRE DATA · " if is_stale(data) else ""
    if review_label(data):
        prefix += review_label(data) + " · "
    fresh = data.get("freshness")
    if not fresh:
        return prefix + data["updated_label"]
    value = timestamp(fresh["source_updated_at"]).astimezone(ZONE)
    return prefix + f"Opdateret {value.day}/{value.month} {value:%H.%M}"


def draw_freshness_corner(draw: ImageDraw.ImageDraw, data: dict[str, Any]) -> None:
    """The update stamp and battery in the bottom-right corner, as on the parents' board.

    Bold when the data is old or partial, so the warning is still noticed.
    """
    from skoleklar.parents_board_render import draw_status
    draw_status(draw, data, bottom=True)


_DAY_NAMES = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag")
_MONTHS = ("jan.", "feb.", "mar.", "apr.", "maj", "jun.", "jul.", "aug.", "sep.", "okt.", "nov.", "dec.")


def weekend_day_title(day: dict[str, Any]) -> str:
    """'Lørdag 3. okt.' for the weekend day the cover shows."""
    value = date.fromisoformat(day["date"])
    return f"{_DAY_NAMES[value.weekday()]} {value.day}. {_MONTHS[value.month - 1]}"


def weekend_state_label(data: dict[str, Any], day: dict[str, Any]) -> str:
    """The cover is today's weather, or tomorrow's once today is over."""
    composed = data.get("freshness", {}).get("composed_at")
    today = timestamp(composed).astimezone(ZONE).date().isoformat() if composed else day["date"]
    return "Weekend" if day["date"] <= today else "Weekend · i morgen"


def weekend_overflow_label(data: dict[str, Any]) -> str:
    """'+2 aktiviteter mere' for weekend activities the two rows cannot hold."""
    count = data.get("weekend_overflow", 0)
    return f"+{count} {'aktivitet' if count == 1 else 'aktiviteter'} mere" if count else ""


def weekend_day_name(day: dict[str, Any]) -> str:
    return _DAY_NAMES[date.fromisoformat(day["date"]).weekday()]


def _clock(value: str) -> float:
    return int(value[:2]) + int(value[3:]) / 60


def sun_arc(day: dict[str, Any]) -> tuple[list[tuple[float, float, str]], tuple[float, float] | None]:
    """The day as fractions of its daylight, 0 at sunrise and 1 at sunset.

    Returns the weather as (start, end, kind) runs and the dry window, both
    clipped to daylight. Neighbouring hours of one kind are merged.
    """
    rise, down = _clock(day["sunrise"]), _clock(day["sunset"])
    span = max(down - rise, 0.1)

    def place(hour: float) -> float:
        return (min(down, max(rise, hour)) - rise) / span

    runs: list[tuple[float, float, str]] = []
    for offset, kind in enumerate(day["hours"]):
        start, end = place(WEATHER_FIRST_HOUR + offset), place(WEATHER_FIRST_HOUR + offset + 1)
        if end <= start:
            continue
        if runs and runs[-1][2] == kind and runs[-1][1] == start:
            runs[-1] = (runs[-1][0], end, kind)
        else:
            runs.append((start, end, kind))
    window = day["window"]
    return runs, (place(window["start"]), place(window["end"])) if window else None


def trip_note(day: dict[str, Any]) -> str:
    """The temperature part of the window label: 'Tør · 11–12°' reads '11–12°'."""
    return day["window"]["label"].split("· ")[-1]


def draw_weekend_panel(draw: ImageDraw.ImageDraw, data: dict[str, Any], live_messages=None) -> None:
    """The left panel at the weekend: the day's weather as a cover, plans, and the messages.

    Nothing about school is shown here; the school day returns on Sunday at noon.
    Activities and message lines look as on the parents' board: a category box,
    `Medbring` in bold, an envelope that is filled when unread.
    """
    from skoleklar import parents_board_render as pb
    day = data["weather"]["days"][0]
    draw.rectangle((0, 0, 339, HEIGHT), fill=PAPER)
    left, end = pb.LEFT_X, pb.LEFT_END
    draw_text(draw, (left, 18), weekend_state_label(data, day).upper(), "small_b", BLACK)
    draw_text(draw, (left, 39), weekend_day_title(day), "hero", BLACK, end - left)

    draw.line((left, 296, end, 296), fill=BLACK, width=1)
    activities = pb.weekend_activities(data)
    # The day column is as wide as its widest label in the font actually used.
    label_width = max((draw.textlength(label, font=styled_font("small_b")[0])
                       for label, _, _ in activities[:2]), default=0)
    y, text_x = 304, left + round(label_width) + 10
    for label, who, items in activities[:2]:
        draw_text(draw, (left, y + 1), label, "small_b", BLACK)
        x = text_x
        if who:
            x = pb._text(draw, x, y, who, "item_b", BLACK) + 6
        for index, item in enumerate(items):
            parts = pb.wrap_item(item, "item", end - x)
            for part in parts:
                pb.draw_item(draw, x, y, part, "item", BLACK, PAPER)
                y += 21
                x = text_x
            if y > 384:
                break
        y += 2
    if not activities:
        draw_text(draw, (left, 306), "Ingen registrerede aktiviteter", "body", BLACK, end - left)
        y = 330
    lines, total = pb.message_lines(data, live_messages)
    top = max(y + 4, 384)
    draw.line((left, top, end, top), fill=BLACK, width=1)
    y = top + 7
    for name, message in lines[:2]:
        pb.draw_message(draw, left, y, end, message, BLACK, PAPER, who=name)
        y += 22
    if total.endswith("i alt"):
        pb._envelope(draw, left, y + 4, BLACK, PAPER, False)
        pb._text(draw, left + 23, y, total, "msg_b", BLACK)
    else:
        pb._text(draw, left, y, total, "msg_b", BLACK)


def draw_weather_panel(draw: ImageDraw.ImageDraw, data: dict[str, Any]) -> None:
    """Weekend weather on the right panel: the verdict, then each day as two half-day
    glyphs above the sun's arc, with the weather along it and the best trip inside."""
    weather = data["weather"]
    days = weather["days"]
    # The frame's white mat is the margin, as on the parents' board.
    left, right = PANEL_SPLIT_X + 14, WIDTH - 10
    column = (right - left + 10) // 2
    cell = (column - 10) // 2

    def glyph(name: str, x: int, y: int, size: int) -> None:
        draw_icon(draw, name, x, y, size, ICON_COLOURS)

    def width_of(value: str, style: str) -> int:
        return int(draw.textlength(value, font=styled_font(style)[0]))

    def centred(value: str, style: str, centre: int, y: int, colour: str) -> None:
        draw_text(draw, (centre - width_of(value, style) // 2, y), value, style, colour)

    draw_text(draw, (left, 24), f"WEEKENDVEJR · {weather['place'].upper()}", "small_b", BLACK, right - left)
    glyph(VERDICT_ICONS[weather["verdict"]], left, 42, 44)
    draw_text(draw, (left + 54, 44), weather["verdict"], "hero", BLACK, right - left - 54)
    draw.line((left, 102, right, 102), fill=BLACK, width=2)
    draw.line((left, 258, right, 258), fill=BLACK, width=2)

    # A single day sits in the middle of the panel instead of the left column.
    first = left if len(days) == 2 else left + (right - left - (column - 10)) // 2
    for index, day in enumerate(days):
        x = first + index * column
        if index:
            draw.line((x - 12, 112, x - 12, 248), fill=BLACK, width=1)
        draw_text(draw, (x, 112), weekend_day_name(day).upper(), "body_b", BLACK, column - 17)
        for cell_index, cell_data in enumerate(day["cells"]):
            cell_x = x + cell_index * cell
            draw_text(draw, (cell_x, 136), cell_data["label"].upper(), "micro", BLACK, cell - 6)
            glyph(cell_data["icon"], cell_x, 154, 56)
            draw_adaptive_text(draw, (cell_x + 60, 162), f"{cell_data['temp']}°",
                               ("temp", "temp_s", "body_b"), BLACK, cell - 62)
            if cell_data["rain_mm"]:
                # Give the amount and its unit the full cell width. Beside the icon,
                # even small totals lose "mm" with the production DejaVu font.
                centred(f"{cell_data['rain_mm']} mm", "micro_b", cell_x + cell // 2 - 3, 216, BLUE)

        # The arc runs from sunrise at its left foot to sunset at its right.
        centre, base, radius = x + cell, 426, 86
        runs, window = sun_arc(day)
        for start, end, kind in runs:
            inset = 5 if kind == "c" else 0
            box = (centre - radius + inset, base - radius + inset, centre + radius - inset, base + radius - inset)
            draw.arc(box, 180 + 180 * start, 180 + 180 * end,
                     fill={"s": YELLOW, "r": BLUE, "c": BLACK}[kind], width=2 if kind == "c" else 11)
        draw.line((centre - radius - 8, base, centre + radius + 8, base), fill=BLACK, width=2)
        if window:
            outer = radius + 9
            draw.arc((centre - outer, base - outer, centre + outer, base + outer),
                     180 + 180 * window[0], 180 + 180 * window[1], fill=BLACK, width=3)
            centred("TUR", "micro_b", centre, base - 64, BLACK)
            centred(f"{day['window']['start']}–{day['window']['end']}", "name", centre, base - 48, BLACK)
            centred(trip_note(day), "micro", centre, base - 20, BLACK)
        else:
            # Plain ink: yellow is for time urgency only.
            centred("Ingen tør tur", "micro_b", centre, base - 44, BLACK)
        glyph("sunrise", centre - radius - 6, base + 6, 20)
        draw_text(draw, (centre - radius + 18, base + 10), day["sunrise"], "micro_b", BLACK)
        glyph("sunset", centre + radius - 14, base + 6, 20)
        draw_text(draw, (centre + radius - 18 - width_of(day["sunset"], "micro_b"), base + 10),
                  day["sunset"], "micro_b", BLACK)


def render_png(data: dict[str, Any], *, live_messages=None,
               dither_mode: str = "hybrid") -> Image.Image:
    from PIL import Image, ImageDraw, ImageFilter

    validate(data)
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    text_mask = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    draw._ag_text_mask = ImageDraw.Draw(text_mask)
    draw.rectangle((0, 0, 340, HEIGHT), fill=BLACK)

    def panel_result() -> Image.Image:
        protected = text_mask.filter(ImageFilter.MaxFilter(3))
        return quantize_for_panel(image, mode=dither_mode, protected_mask=protected)

    hero = data["hero"]
    if data.get("day_view"):
        # The children's day: weather, artwork and what to remember; the lessons per child.
        from skoleklar.day_view_render import draw_day_view, panel_artwork_preset
        from skoleklar.weekend_art import ART_ORIGIN, panel_artwork
        draw_day_view(draw, data)
        draw_freshness_corner(draw, {**data, "review_count": 0})
        panel = panel_result()
        panel.paste(panel_artwork(panel_artwork_preset(data)), ART_ORIGIN)
        return panel
    if data.get("weather"):
        # Weekend: activities on the left, weather on the right; no school-week table.
        draw_weekend_panel(draw, data, live_messages)
        draw_weather_panel(draw, data)
        draw_freshness_corner(draw, data)
        panel = panel_result()
        from skoleklar.weekend_art import ART_ORIGIN, panel_artwork, select_artwork
        # Paste after dashboard conversion to preserve all six artwork pigments
        # and keep diffusion out of the text and the right-hand forecast.
        panel.paste(panel_artwork(select_artwork(data["weather"]["days"][0])), ART_ORIGIN)
        return panel
    if data["schema_version"] == 2:
        # One row per child: the next school day on the left, the days ahead on the right.
        from skoleklar.parents_board_render import draw_parents_board
        draw_parents_board(draw, data, live_messages)
        return panel_result()
    draw_hero_panel(draw, data, live_messages)
    draw_freshness_corner(draw, data)

    # The week's dates, as in the browser, so the day rows can use short labels.
    week_heading = short_date(data.get("horizon_label", "")) or "SKOLEUGEN"
    draw_adaptive_text(draw, (368, 24), week_heading, ("section", "body_b"), BLACK, 404)
    draw_text(draw, (448, 54), hero["children"][0]["name"], "small_b", BLACK, 153)
    draw_text(draw, (619, 54), hero["children"][1]["name"], "small_b", BLACK, 153)

    if data["schema_version"] == 2:
        y = 76
        forecast = day_weather(data)
        for layout in eink_weekday_layout(data, draw, available_height=309):
            day = layout["day"]
            draw.line((368, y - 5, 772, y - 5), fill=BLACK, width=1)
            if layout["folded"]:
                # 'ONS. 11/11' fits the day column; the summary starts on the child column.
                draw_text(draw, (368, y + 3), compact_panel_date(day["day"]).upper(), "micro_b", BLACK, 76)
                draw_text(draw, (448, y + 3), day["summary"], "micro", BLACK, 324)
            else:
                label = day["day"].split(maxsplit=1)
                draw_text(draw, (368, y + 2), label[0].upper(), "small_b", BLACK, 62)
                if len(label) > 1:
                    draw_text(draw, (368, y + 18), label[1], "small", BLACK, 62)
                if day.get("date") in forecast:
                    draw_icon(draw, forecast[day["date"]]["icon"], 418, y - 1, 22, ICON_COLOURS)
                for child_index, child in enumerate(day["children"][:2]):
                    x = 448 + child_index * 171
                    heading = display_heading(child)
                    if heading in PREPARE_HEADINGS:
                        draw_marked_text(draw, (x, y + 2), heading, "body_b", 153)
                    elif heading:
                        draw_text(draw, (x, y + 2), heading, "body_b", BLUE, 153)
                    line_top = y + 20 if heading else y + 2
                    for row, (offset, line) in enumerate(_eink_child_lines(draw, child)):
                        draw_text(draw, (x + offset, line_top + row * 17), line, "micro", BLACK,
                                  153 - offset)
                if day.get("shared"):
                    shared = wrapped_lines(draw, day["shared"], 324, "micro_b", limit=2)
                    # Six pixels above the row's edge, so the marker clears the next rule.
                    shared_top = y + layout["height"] - len(shared) * 17 - 6
                    for row, line in enumerate(shared):
                        draw_marked_text(draw, (448, shared_top + row * 17), line, "micro_b", 324)
            y += layout["height"]
        draw.line((368, 386, 772, 386), fill=BLACK, width=2)
        draw_text(draw, (368, 394), "WEEKEND", "micro_b", BLUE)
        overflow = weekend_overflow_label(data)
        if overflow:
            # An aside on the right of the heading row, so it never reads as a column title.
            overflow_font = styled_font("micro")[0]
            draw_text(draw, (772 - int(draw.textlength(overflow, font=overflow_font)), 394),
                      overflow, "micro", BLACK)
        rows = weekend_rows(data)
        if rows:
            # One activity per row across both child columns, named by its bold prefix.
            names = [child["name"] for child in hero["children"]]
            for row_index, item in enumerate(rows[:2]):
                item_y = 415 + row_index * 21
                draw_text(draw, (368, item_y), compact_panel_date(item["day"]).upper(),
                          "micro_b", BLACK, 76)
                prefix, text = weekend_row_parts(item, names)
                x = 448
                if prefix:
                    draw_text(draw, (x, item_y), prefix, "micro_b", BLACK, 324)
                    x += int(draw.textlength(prefix, font=styled_font("micro_b")[0])) + 4
                draw_text(draw, (x, item_y), text, "micro", BLACK, 772 - x)
        else:
            draw_text(draw, (448, 415), "Ingen registrerede aktiviteter", "micro", BLACK, 324)
    else:
        for index, day in enumerate(data.get("horizon", [])):
            y = 100 + index * 90
            draw.line((368, y - 8, 772, y - 8), fill=BLACK, width=1)
            label = day["day"].split(maxsplit=1)
            draw_text(draw, (368, y), label[0].upper(), "small_b", BLACK, 62)
            if len(label) > 1:
                draw_text(draw, (368, y + 18), label[1], "small", BLACK, 62)
            for child_index, child in enumerate(day.get("children", [])[:2]):
                x = 448 + child_index * 171
                heading = display_heading(child)
                if heading in PREPARE_HEADINGS:
                    draw_marked_text(draw, (x, y), heading, "body_b", 153)
                elif heading:
                    draw_text(draw, (x, y), heading, "body_b", BLUE, 153)
                line_top = y + 22 if heading else y
                for row, (offset, line) in enumerate(_eink_child_lines(draw, child)):
                    draw_text(draw, (x + offset, line_top + row * 18), line, "small", BLACK,
                              153 - offset)
            if day.get("shared"):
                draw_marked_text(draw, (448, y + 60), day["shared"], "small_b", 324)

        later = data.get("later", [])
        if later:
            draw.line((368, 365, 772, 365), fill=BLACK, width=2)
            draw_text(draw, (368, 374), "DEREFTER", "micro_b", BLACK)
            for row, item in enumerate(later):
                y = 394 + row * 20
                draw_text(draw, (368, y), item["day"].upper(), "micro_b", BLACK, 92)
                draw_text(draw, (466, y), item["summary"], "micro", BLACK, 306)
            footer_y = 449
        else:
            footer_y = 432
        if data.get("footer"):
            draw.line((368, footer_y - 8, 772, footer_y - 8), fill=BLACK, width=2)
            draw_text(draw, (368, footer_y), data["footer"], "micro_b", BLACK, 404)

    return panel_result()


DASHBOARD_CSS = """
/* Sizes are written as Nu: N design pixels of the 800x480 panel. The panel is a
   size container, so 1u scales with its width (and is a plain 1px when stacked). */
*{box-sizing:border-box}
:root{color-scheme:light}
@FONTS@
html,body{margin:0;background:@PAGE@;color:@INK@;font-family:"Helvetica Neue",Helvetica,Arial,sans-serif}
body{min-height:100vh;display:grid;grid-template-columns:minmax(0,1fr);place-items:center;padding:24px}
.frame{width:min(100%,800px);container-type:inline-size}
.dashboard{
 --u:calc(100cqw/800);
 --serif:"Newsreader Embedded",Georgia,"Iowan Old Style","Palatino Linotype","DejaVu Serif",serif;
 --sans:"Helvetica Neue",Helvetica,Arial,sans-serif;
 --gap:16u;
 --day-col:56u;
 --col-a:minmax(0,1fr);
 --col-b:minmax(0,1fr);
 /* No overflow clipping and no fixed height: the panel is 5:3 at minimum and
    grows if content needs more. Nothing is ever cut off. */
 aspect-ratio:5/3;
 display:grid;grid-template-columns:360fr 440fr;
 grid-template-rows:minmax(min-content,1fr);
 background:@PAPER@;box-shadow:0 18px 60px #191e2133;
 font-size:12u;line-height:1.3;font-variant-numeric:lining-nums;
 text-wrap:pretty;overflow-wrap:anywhere;
}
.hero{position:relative;display:flex;flex-direction:column;background:@INK@;color:@PAGE@;padding:0 24u;min-width:0}
/* Today's weather at a glance: one glyph and the temperature range, top right. */
.wx-today{position:absolute;top:17u;right:24u;display:flex;align-items:center;gap:6u;font-size:14u;font-weight:700}
.wx-today .wx-icon{width:30u;height:30u}
.hero-top{padding:24u 0 12u}
.hero-foot{margin-top:auto;padding-bottom:14u}
.hero-view{display:none}
.hero-view[data-default]{display:block}
.weekend-view h1{margin-bottom:18u}
.weekend-panel{border-top:1px solid @ICE@;padding-top:12u}
.weekend-panel ul{list-style:none;margin:0;padding:0;display:grid;gap:12u}
.weekend-panel li{display:grid;grid-template-columns:82u minmax(0,1fr);gap:10u;
 font:15u/1.35 var(--serif);padding-bottom:10u;border-bottom:1px solid #ffffff33}
.weekend-panel li b{color:@ICE@;font:700 11u/1.3 var(--sans);text-transform:uppercase}
.weekend-empty{font:italic 15u/1.4 var(--serif);margin:0}
.messages{margin:0}
.state{font-size:11u;font-weight:700;letter-spacing:.14em;text-transform:uppercase;opacity:.75}
.stale{color:@MARK@;font-size:11u;font-weight:700;letter-spacing:.06em;margin-bottom:8u}
h1{font:700 34u/1.1 var(--serif);margin:3u 0 22u;text-wrap:balance}
.hero-grid,.message-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:20u}
.child{min-width:0}
.child h2{font:700 21u/1.2 var(--serif);margin:0 0 9u}
.child strong,.child span{display:block}
.category{color:@INDIGO@;font:500 12u/1.3 var(--sans);margin-bottom:5u}
.hero .category{color:@ICE@}
/* Attention without red: ink on a straw marker, on paper and on the dark hero alike. */
.category.prepare,.hero .category.prepare,.shared{background:@MARK@;color:@INK@;width:fit-content;padding:0 4u;font-weight:700}
/* Items: tight leading inside an item, a clear gap between items, and a hanging
   indent so a wrapped line is visibly the same item (no more "two lines" look). */
.child span{font:13u/1.32 var(--serif);padding-left:9u;text-indent:-9u;margin-top:4u}
.child .category+span{margin-top:0}
.hero-grid .child span{font-size:12.5u}
.weekend{margin-top:14u;border-top:1px solid #e8e8e866;padding-top:8u}
.weekend h3,.later h3,.week-weekend h3{margin:0 0 4u;color:@ICE@;font-size:10u;text-transform:uppercase;letter-spacing:.14em}
.week-weekend h3{color:@INDIGO@}
.weekend ul,.later ul,.week-weekend ul{list-style:none;margin:0;padding:0}
.weekend li,.later li{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8u;font-size:11u;padding:2u 0}
.messages{border-top:1px solid @PAGE@;padding-top:9u}
.messages h3{display:flex;justify-content:space-between;align-items:baseline;color:@ICE@;font-size:11u;text-transform:uppercase;letter-spacing:.14em;margin:0 0 7u}
.messages ul{list-style:none;padding:0;margin:0}
.messages li{min-width:0;font-size:11u}
.messages li>*{display:block}
.messages .message-child{font-size:14u;margin-bottom:3u}
.messages small{font-size:9u;opacity:.82;margin-bottom:3u}
.messages .live-message .subject{font:italic 12u/1.3 var(--serif);overflow:hidden;margin-top:4u;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
.updated{margin:8u 0 0;font-size:9u;font-weight:400;letter-spacing:.02em;color:@PAGE@;opacity:.55}

/* Weekend mode: the day's weather as a cover and the plans on the left, weather on the right. */
.wx-cover{margin:-14u 0 10u;text-align:center}
.wx-cover .wx-icon{width:180u;height:180u;margin:0 auto}
.hero.weekend-art{background:@PAPER@;color:@INK@}
.weekend-art .hero-top{margin:0 -24u;padding:18u 0 8u}
.weekend-art .hero-top>.state,.weekend-art .stale{margin-left:24u;margin-right:24u}
.weekend-art .state,.weekend-art .messages h3,.weekend-art .wk-list b{color:@INDIGO@;opacity:1}
.weekend-art h1{font-size:30u;margin:3u 24u 10u}
.weekend-art .wx-cover{margin:0 0 12u;text-align:left}
.weekend-art .wx-cover img{display:block;width:100%;height:auto;aspect-ratio:340/200;
 mask-image:linear-gradient(to bottom,transparent,#000 10%)}
.weekend-art .messages,.weekend-art .wk-list,.weekend-art .wk-none{border-top-color:@INK@}
.weekend-art .wk-list,.weekend-art .wk-none{margin-left:24u;margin-right:24u}
.weekend-art .updated{color:@INK@}
.weekend-art .stale{color:@INK@;background:@MARK@}
.wk-list,.wk-none{border-top:1px solid #ffffff66;margin:0;padding:9u 0 0}
.wk-list{list-style:none}
.wk-list li{display:grid;grid-template-columns:40u minmax(0,1fr);column-gap:8u;align-items:baseline;margin-bottom:3u}
.wk-list b{color:@ICE@;font-size:11u;letter-spacing:.14em;text-transform:uppercase}
.wk-list span,.wk-none{font:15u/1.3 var(--serif)}
.wk-none{font-style:italic}
.messages .arrivals li{font-size:12u;margin-bottom:1u}
.weather{padding:22u 28u 14u;min-width:0;display:flex;flex-direction:column}
.weather .state{color:@INDIGO@;opacity:1}
.wx-icon{display:block;flex:none}
.wx-verdict{display:flex;align-items:center;gap:10u;margin:4u 0 6u;font:700 34u/1.1 var(--serif)}
.wx-verdict .wx-icon{width:44u;height:44u}
.wx-days{flex:1;display:grid;grid-template-columns:repeat(auto-fit,minmax(0,1fr));column-gap:21u;border-top:2px solid @INK@;padding-top:8u}
.wx-day{position:relative;min-width:0;display:flex;flex-direction:column}
/* A single day keeps the width of one column, centred. */
.wx-day:only-child{width:50%;justify-self:center}
/* Equal columns: the divider hangs in the gap instead of taking width from a day. */
.wx-day+.wx-day::before{content:"";position:absolute;left:-11u;top:0;bottom:0;border-left:1px solid @INK@}
.wx-day h3{margin:0;font-size:13u;line-height:1.2;text-transform:uppercase}
.wx-cells{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:8u;margin-top:8u;padding-bottom:12u;border-bottom:2px solid @INK@}
.wx-cell{display:grid;grid-template-columns:44u minmax(0,1fr);column-gap:5u;align-content:start;min-width:0}
.wx-cell small{grid-column:1/-1;font-size:9u;font-weight:500;letter-spacing:.06em;text-transform:uppercase;margin-bottom:3u;white-space:nowrap;overflow-wrap:normal}
.wx-cell .wx-icon{width:44u;height:44u;grid-row:2/4}
.wx-cell b{font-size:24u;line-height:1.05;align-self:end;white-space:nowrap;overflow-wrap:normal}
.wx-cell span{color:@INDIGO@;font-size:10u;font-weight:700;line-height:1.2;min-height:12u;white-space:nowrap;overflow-wrap:normal}
/* The day as the sun's arc: sunrise at the left foot, sunset at the right, the weather
   painted along it and the best trip named inside. Cloud is a thin line, so the arc
   also reads without colour. */
.wx-arc{margin:auto 0;padding-top:14u}
.wx-arc svg{display:block;width:100%;overflow:visible}
.wx-arc path{fill:none;stroke:@INK@;stroke-width:2}
.wx-arc path.s{stroke:@SUN@;stroke-width:11}
.wx-arc path.r{stroke:@INDIGO@;stroke-width:11}
.wx-arc path.trip{stroke-width:3}
.wx-arc text{text-anchor:middle;fill:@INK@;font:400 11px var(--sans)}
.wx-arc text.label{fill:@INDIGO@;font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}
.wx-arc text.span{font:700 20px var(--serif)}
.wx-arc text.none{font-weight:700}
.wx-arc .mark{fill:@MARK@}
.wx-arc figcaption{display:flex;justify-content:space-between;font-size:11u;font-weight:700;margin-top:4u}
.wx-arc figcaption span{display:flex;align-items:center;gap:4u}
.wx-arc figcaption .wx-icon{width:17u;height:17u}

/* One column template for the whole week panel: labels, day rows and the weekend
   band are subgrids of it, so their columns can never drift apart. The week has
   two rows of its own: the table (takes all spare height) and the weekend band,
   which is only as tall as its content and sits on the bottom edge. */
.week{display:grid;grid-template-rows:1fr auto;padding:0 28u;min-width:0;
 grid-template-columns:var(--day-col) var(--col-a) var(--col-b);column-gap:var(--gap)}
.week-top{grid-row:1;grid-column:1/-1;display:grid;grid-template-columns:subgrid;
 grid-template-rows:auto auto minmax(min-content,1fr) auto;padding:22u 0 8u}
.week-top>h2{grid-column:1/-1;grid-row:1;font:700 16u/1.2 var(--serif);margin:0 0 6u}
.labels{grid-column:1/-1;grid-row:2;display:grid;grid-template-columns:subgrid;font-size:10u;font-weight:500;letter-spacing:.14em;text-transform:uppercase;padding-bottom:4u;border-bottom:1px solid @INK@}
.labels span:first-child{grid-column:2}
.days{grid-column:1/-1;grid-row:3;display:grid;grid-template-columns:subgrid;align-content:start}
.day{grid-column:1/-1;display:grid;grid-template-columns:subgrid;align-items:start;
 border-top:1px solid #191e2126;padding:6u 0 5u}
.labels+.days>.day:first-child{border-top:0}
/* The label owns the day column's first row, so a folded day's cells and its shared
   note can never be placed into the day column. */
.day h3{grid-column:1;grid-row:1;margin:2u 0;font-size:12u;font-weight:500;line-height:1.2;text-transform:uppercase}
/* The day's weather as one glyph under its date; folded days leave it out. */
.day h3 .wx-icon{width:22u;height:22u;margin:3u 0 0}
.day.folded h3 .wx-icon{display:none}
.day-grid{display:contents}
.day .child h2{display:none}
.day .child{grid-column:auto}
.day .child span{font-size:12.5u;margin-top:3u}
.day .category{font-size:11u;margin:0 0 1u}
.shared{grid-column:2/-1;font-size:11u;margin:3u 0 0}
.folded-cell{display:none;margin:0;font:12u/1.3 var(--serif);color:@INK@;align-self:center}
/* A folded day is a tinted band: the day label and one short, muted line per child.
   The band is painted with box-shadow so no horizontal padding disturbs the subgrid. */
.day.folded{padding:6u 0;background:@MIST@;box-shadow:-8px 0 0 @MIST@,8px 0 0 @MIST@;border-top-color:transparent;
 border-bottom:1px solid #191e2114}
.day.folded h3{font-size:10u;margin:0;align-self:center;color:@INK@}
/* Attention by tone: a folded day that is still ahead reads darker than one that is
   already over, so the reader can tell "collapsed but coming" from "done". */
.day.folded[data-past] h3,.day.folded[data-past] .folded-cell{color:@SLATE@}
.day.folded .day-grid{display:none}
.day.folded .folded-cell{display:block;grid-row:1}
.day.folded .shared{grid-column:2/-1;grid-row:2;margin:2u 0 0}
.later{grid-column:1/-1;grid-row:4;border-top:2px solid @INK@;margin-top:5u;padding-top:6u}
.later h3{color:@INK@}
.later li{font-size:10u;line-height:1.2}
.week-weekend{grid-row:2;grid-column:1/-1;display:grid;grid-template-columns:subgrid;align-content:start;
 border-top:1px solid @INK@;padding:8u 0 12u}
/* The heading on its own row, with any overflow as an aside on the right. Each
   activity then puts its day in the day column and spans both child columns, with
   whose it is as a bold prefix, so one child's long row never stops at a column. */
.week-weekend h3{grid-column:1/-1;grid-row:1;display:flex;justify-content:space-between;align-items:baseline;
 margin:0 0 2u;font-size:9u;letter-spacing:.06em;white-space:nowrap;overflow-wrap:normal}
.week-weekend h3 span{color:@INK@;font:italic 400 11u/1.3 var(--serif);letter-spacing:0;text-transform:none}
.week-weekend ul{grid-column:1/-1;display:grid;grid-template-columns:subgrid}
.week-weekend li{display:grid;grid-template-columns:subgrid;grid-column:1/-1;align-items:baseline;font:12u/1.3 var(--serif);padding:0}
.week-weekend li>b{grid-column:1;font:700 10u var(--sans);text-transform:uppercase;white-space:nowrap;overflow-wrap:normal}
.week-weekend li>span{grid-column:2/-1}
.week-weekend p{grid-column:2/-1;font:italic 12u/1.3 var(--serif);margin:0}

/* Narrow containers stack the panels and use real pixels instead of scaling. */
@container (max-width:680px){
 .dashboard{--u:1px;aspect-ratio:auto;grid-template-columns:minmax(0,1fr);grid-template-rows:none}
 .hero-foot{margin-top:32px}
 .week{grid-template-rows:none}
}
@container (max-width:420px){
 .wx-days{grid-template-columns:minmax(0,1fr)}
 .wx-day:only-child{width:100%}
 .wx-day+.wx-day{border-top:1px solid @INK@;margin-top:16u;padding-top:16u}
 .wx-day+.wx-day::before{display:none}
 .wx-arc{width:min(100%,220px);align-self:center;margin:14u auto 0}
 .weekend-art .messages h3{flex-wrap:wrap;gap:4u}
}
"""
# Folds weekday rows by measurement. Days that are already over (data-past) are always
# folded. Every other day starts expanded; days that carry
# data-fold-rank (past days first, then the farthest ahead) fold one at a time until
# the 5:3 panel fits, then any that fold needlessly are opened again. The two pinned days (no data-fold-rank) never fold. It stops
# when the hero column is what makes the panel too tall (folding would not help) and
# does nothing when stacked (height is then unbounded). The script is a constant with
# no interpolated data, so a CSP hash can allow it: see FOLD_SCRIPT_CSP_HASH.
FOLD_SCRIPT = """
(function(){
var panel=document.querySelector('.dashboard'),days=document.querySelector('.days');
if(!panel||!days)return;
function localParts(now){var p={},parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Copenhagen',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(now);parts.forEach(function(x){if(x.type!=='literal')p[x.type]=Number(x.value)});return p}
function iso(y,m,d){return y+'-'+String(m).padStart(2,'0')+'-'+String(d).padStart(2,'0')}
function add(p,n){var d=new Date(Date.UTC(p.year,p.month-1,p.day+n));return iso(d.getUTCFullYear(),d.getUTCMonth()+1,d.getUTCDate())}
function selectHero(){
 var raw=panel.getAttribute('data-now'),p=localParts(raw?new Date(raw):new Date()),weekday=new Date(Date.UTC(p.year,p.month-1,p.day)).getUTCDay(),late=p.hour>=15,noon=p.hour>=12;
 var weekend=(weekday===5&&late)||weekday===6||(weekday===0&&!noon),target=iso(p.year,p.month,p.day);
 if(!weekend&&((weekday>=1&&weekday<=4&&late)||weekday===0))target=add(p,1);
 var views=[].slice.call(panel.querySelectorAll('.hero-view')),chosen=weekend?panel.querySelector('[data-weekend-view]'):panel.querySelector('[data-school-date="'+target+'"]');
 if(!chosen)chosen=panel.querySelector('.hero-view[data-default]');
 views.forEach(function(v){v.style.display=v===chosen?'block':'none'});
 var hero=panel.querySelector('.hero');hero.classList.toggle('weekend-mode',!!(chosen&&chosen.hasAttribute('data-weekend-view')));
 if(chosen&&!weekend){var state=chosen.querySelector('.state');if(state)state.textContent=target===iso(p.year,p.month,p.day)?'I DAG':'NÆSTE SKOLEDAG'}
}
selectHero();
var rows=[].slice.call(days.querySelectorAll('.day'));
var byRank=rows.filter(function(r){return r.hasAttribute('data-fold-rank')}).sort(function(a,b){return a.dataset.foldRank-b.dataset.foldRank});
if(!byRank.length)return;
function apply(){days.style.gridTemplateRows=rows.map(function(r){return r.classList.contains('folded')?'min-content':'auto'}).join(' ')}
function natural(el){el.style.alignSelf='start';var h=el.getBoundingClientRect().height;el.style.alignSelf='';return h}
function fit(){
 var stacked=getComputedStyle(panel).gridTemplateColumns.split(' ').length<2;
 rows.forEach(function(r){r.classList.toggle('folded',r.hasAttribute('data-past'))});apply();
 if(stacked)return;
 var target=panel.getBoundingClientRect().width*3/5;
 var heroNeeds=natural(panel.querySelector('.hero-top'))+natural(panel.querySelector('.hero-foot')),week=panel.querySelector('.week');
 // Folding only helps while the week column is taller than the hero column.
 for(var i=0;i<byRank.length&&panel.getBoundingClientRect().height>target+0.5&&natural(week)>heroNeeds+0.5;i++){byRank[i].classList.add('folded');apply()}
 // Folding can overshoot; give room back, keeping the days that fold first folded.
 for(var j=byRank.length-1;j>=0;j--){var row=byRank[j];
  if(row.hasAttribute('data-past')||!row.classList.contains('folded'))continue;
  row.classList.remove('folded');apply();
  if(panel.getBoundingClientRect().height>target+0.5){row.classList.add('folded');apply();break}}
}
var width=0;
function refit(){var w=panel.getBoundingClientRect().width;if(w!==width){width=w;fit()}}
refit();
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(function(){width=0;refit()});
if(window.ResizeObserver)new ResizeObserver(refit).observe(panel.parentNode);
})();
"""
FOLD_SCRIPT_CSP_HASH = "sha256-" + base64.b64encode(hashlib.sha256(FOLD_SCRIPT.encode("utf-8")).digest()).decode("ascii")

HTML_TEMPLATE = (
    '<!doctype html>\n<html lang="da"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Skoleoverblik</title><style>{{CSS}}</style></head>'
    '<body><div class="frame"><main class="dashboard">{{BODY}}</main></div>'
    '<script>{{SCRIPT}}</script></body></html>'
)


@lru_cache(maxsize=1)
def _font_faces() -> str:
    """Embed Newsreader so wrapping and folding match on every machine."""
    folder = Path(__file__).with_name("fonts")
    faces = []
    for weight in (400, 700):
        data = (folder / f"newsreader-latin-{weight}.woff2").read_bytes()
        faces.append(
            '@font-face{font-family:"Newsreader Embedded";font-style:normal;'
            f"font-weight:{weight};font-display:block;"
            f'src:url(data:font/woff2;base64,{base64.b64encode(data).decode("ascii")}) format("woff2")}}'
        )
    return "\n".join(faces)


def _dashboard_css(extra: str = "") -> str:
    css = DASHBOARD_CSS + extra
    for token, value in HTML_PALETTE.items():
        css = css.replace(f"@{token}@", value)
    css = re.sub(r"(?<![\w.#])(-?\d+(?:\.\d+)?)u\b", r"calc(\1*var(--u))", css)
    # Insert the fonts after the unit rewrite so base64 data is never touched.
    return css.replace("@FONTS@", _font_faces())


def esc(value: Any) -> str:
    return html.escape(str(value))


_MONTH_ABBREVIATIONS = {
    "januar": "jan.", "februar": "feb.", "marts": "mar.", "april": "apr.", "maj": "maj",
    "juni": "jun.", "juli": "jul.", "august": "aug.", "september": "sep.", "oktober": "okt.",
    "november": "nov.", "december": "dec.",
}
_MONTH_AFTER_DAY = re.compile(r"(?<=\d\. )(" + "|".join(_MONTH_ABBREVIATIONS) + r")\b", re.I)


def short_date(value: str) -> str:
    """'30. september' -> '30. sep.' (only after a day number, so names are safe)."""
    return _MONTH_AFTER_DAY.sub(lambda m: _MONTH_ABBREVIATIONS[m.group(1).lower()], value)


def compact_panel_date(value: str) -> str:
    """Use a numeric month where the fixed e-ink day column is narrow."""
    result = short_date(value)
    for month, abbreviation in enumerate((
        "jan.", "feb.", "mar.", "apr.", "maj", "jun.",
        "jul.", "aug.", "sep.", "okt.", "nov.", "dec.",
    ), start=1):
        result = re.sub(rf"(\d{{1,2}})\.\s+{re.escape(abbreviation)}", rf"\1/{month}",
                        result, flags=re.I)
    return result


def short_freshness(data: dict[str, Any]) -> str:
    """A corner-sized update stamp, e.g. 'opd. 29/9 22:07'."""
    fresh = data.get("freshness")
    review = review_label(data) + " · " if review_label(data) else ""
    if not fresh:
        return review + data["updated_label"]
    value = timestamp(fresh["source_updated_at"])
    return f"{review}opd. {value.day}/{value.month} {value:%H:%M}"


_SUBSTITUTE_LINE = re.compile(r"^Vikar:\s*(?P<teacher>[^·]+?)\s*·\s*(?P<lesson>.+)$", re.I)


def short_substitute(line: str) -> str:
    """'Vikar: Thorbjørn Z. G. · DAN 00' -> 'Vikar Thorbjørn · DAN 00'.

    The state keeps the full teacher, subject and class (that is validated); the
    display keeps the first name, subject and class so the item fits one line.
    """
    match = _SUBSTITUTE_LINE.match(str(line).strip())
    if not match:
        return line
    first_name = match.group("teacher").split()[0]
    return f"Vikar {first_name} · {match.group('lesson').strip()}"


def display_lines(child: dict[str, Any]) -> list[str]:
    """Drop lines that only repeat the heading or an earlier line."""
    heading = display_heading(child).casefold()
    seen = {heading} if heading else set()
    result = []
    for line in (short_substitute(raw) for raw in child.get("lines", [])):
        key = " ".join(str(line).split()).casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(line)
    return result


def folded_cell(child: dict[str, Any]) -> str:
    """One short line for a folded day: the child's own heading, else a dash."""
    heading = str(child.get("heading", "")).strip()
    return heading if heading and heading.casefold() != "ingen punkter" else "–"


def child_html(child: dict[str, Any]) -> str:
    lines = "".join(f"<span>{esc(line)}</span>" for line in display_lines(child))
    heading = display_heading(child)
    tone = " prepare" if heading in PREPARE_HEADINGS else ""
    category = f'<strong class="category{tone}">{esc(heading)}</strong>' if heading else ""
    return f'<section class="child"><h2>{esc(child["name"])}</h2>{category}{lines}</section>'


def weekday_child_html(child: dict[str, Any]) -> str:
    # One span per logical line. The browser wraps; the layout never guesses.
    return child_html(child)


_HERO_WEEKDAYS = {
    "man": "Mandag", "tir": "Tirsdag", "ons": "Onsdag",
    "tor": "Torsdag", "fre": "Fredag",
}


def hero_day_label(value: str) -> str:
    """Expand a compact weekday label for the large left-panel heading."""
    first, separator, rest = str(value).partition(" ")
    expanded = _HERO_WEEKDAYS.get(first.rstrip(".").casefold(), first)
    return f"{expanded} {rest}" if separator else expanded


def today_weather_html(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    return (f'<div class="wx-today">{icon_svg(item["icon"], HTML_HERO_ICON_COLOURS)}'
            f'{esc(temperature_range(item))}</div>')


def _hero_views(data: dict[str, Any], hero: dict[str, Any]) -> str:
    """Embed each available school day plus the weekend for clock-based selection."""
    if data["schema_version"] != 2:
        children = "".join(child_html(child) for child in hero["children"])
        return (
            f'<div class="hero-view" data-default><div class="state">{esc(hero["state"])}</div>'
            f'<h1>{esc(short_date(hero["day"]))}</h1><div class="hero-grid">{children}</div>'
            "</div>"
        )

    composed = timestamp(data["freshness"]["composed_at"])
    detailed_date = dashboard_focus(composed).school_date.isoformat()
    forecast = day_weather(data)
    views = []
    for day in data["weekdays"]:
        is_default = day["date"] == detailed_date
        children_data = hero["children"] if is_default else day["children"]
        children = "".join(child_html(child) for child in children_data)
        label = hero["day"] if is_default else hero_day_label(day["day"])
        default = " data-default" if is_default else ""
        state = hero["state"] if is_default else "Skoledag"
        views.append(
            f'<div class="hero-view" data-school-date="{esc(day["date"])}"{default}>'
            f'{today_weather_html(forecast.get(day["date"]))}'
            f'<div class="state">{esc(state)}</div><h1>{esc(short_date(label))}</h1>'
            f'<div class="hero-grid">{children}</div></div>'
        )
    if not any(" data-default" in view for view in views):
        children = "".join(child_html(child) for child in hero["children"])
        views.insert(0, (
            f'<div class="hero-view" data-default><div class="state">{esc(hero["state"])}</div>'
            f'<h1>{esc(short_date(hero["day"]))}</h1><div class="hero-grid">{children}</div></div>'
        ))

    rows = "".join(
        f'<li><b>{esc(item["day"])}</b><span>{esc(item["text"])}</span></li>'
        for item in data.get("weekend", [])
    )
    content = (
        f'<ul>{rows}</ul>' if rows
        else '<p class="weekend-empty">Ingen registrerede weekendaktiviteter</p>'
    )
    views.append(
        '<div class="hero-view weekend-view" data-weekend-view>'
        '<div class="state">Weekend</div><h1>Weekend</h1>'
        f'<section class="weekend-panel">{content}</section></div>'
    )
    return "".join(views)


def _arc_path(start: float, end: float, radius: float) -> str:
    """An SVG arc over the top of a half circle centred on (100, 100)."""
    def point(fraction: float) -> str:
        angle = math.pi * fraction
        return f"{100 - radius * math.cos(angle):.1f} {100 - radius * math.sin(angle):.1f}"
    return f"M{point(start)}A{radius:g} {radius:g} 0 0 1 {point(end)}"


def sun_arc_svg(day: dict[str, Any]) -> str:
    runs, window = sun_arc(day)
    parts = [
        f'<path class="{kind}" d="{_arc_path(start, end, 79 if kind == "c" else 84)}"/>'
        for start, end, kind in runs
    ]
    parts.append('<path class="ground" d="M8 100H192"/>')
    if window:
        parts.append(f'<path class="trip" d="{_arc_path(window[0], window[1], 94)}"/>')
        parts.append('<text class="label" x="100" y="48">Tur</text>'
                     f'<text class="span" x="100" y="70">{day["window"]["start"]}–{day["window"]["end"]}</text>'
                     f'<text class="note" x="100" y="86">{esc(trip_note(day))}</text>')
    else:
        parts.append('<rect class="mark" x="56" y="58" width="88" height="17"/>'
                     '<text class="none" x="100" y="71">Ingen tør tur</text>')
    return f'<svg viewBox="0 0 200 104" aria-hidden="true">{"".join(parts)}</svg>'


def weather_html(weather: dict[str, Any]) -> str:
    """The weekend weather panel that takes the school week's place on the right."""
    def glyph(name: str) -> str:
        return icon_svg(name, HTML_ICON_COLOURS)

    days = []
    for day in weather["days"]:
        cells = "".join(
            f'<div class="wx-cell"><small>{esc(cell["label"])}</small>{glyph(cell["icon"])}'
            f'<b>{cell["temp"]}°</b><span>{str(cell["rain_mm"]) + " mm" if cell["rain_mm"] else ""}</span></div>'
            for cell in day["cells"]
        )
        days.append(
            f'<article class="wx-day"><h3>{esc(weekend_day_name(day))}</h3>'
            f'<div class="wx-cells">{cells}</div>'
            f'<figure class="wx-arc">{sun_arc_svg(day)}'
            f'<figcaption><span>{glyph("sunrise")}{esc(day["sunrise"])}</span>'
            f'<span>{esc(day["sunset"])}{glyph("sunset")}</span></figcaption></figure></article>'
        )
    return (
        f'<section class="weather"><div class="state">Weekendvejr · {esc(weather["place"])}</div>'
        f'<h2 class="wx-verdict">{glyph(VERDICT_ICONS[weather["verdict"]])}{esc(weather["verdict"])}</h2>'
        f'<div class="wx-days">{"".join(days)}</div></section>'
    )


def weekend_hero_html(data: dict[str, Any]) -> str:
    """The left panel at the weekend: the day's weather as a cover, then the plans."""
    day = data["weather"]["days"][0]
    from skoleklar.weekend_art import artwork_data_uri, select_artwork
    preset = select_artwork(day)
    from skoleklar.parents_board_render import item_html, weekend_activities
    rows = []
    for label, who, items in weekend_activities(data):
        whose = f"<b>{esc(who)}</b> " if who else ""
        lines = "<br>".join(item_html(item) for item in items)
        rows.append(f'<li><b>{esc(label)}</b><span>{whose}{lines}</span></li>')
    activities = (f'<ul class="wk-list">{"".join(rows)}</ul>' if rows
                  else '<p class="wk-none">Ingen registrerede aktiviteter</p>')
    return (
        f'<div class="state">{esc(weekend_state_label(data, day))}</div><h1>{esc(weekend_day_title(day))}</h1>'
        f'<figure class="wx-cover" data-artwork="{preset}">'
        f'<img src="{artwork_data_uri(preset)}" width="680" height="400" '
        'alt="Huset og haven i årstidens vejr">'
        f'</figure>{activities}'
    )


# The weekend in the parents' board's terms: blue only for category boxes, yellow
# only for time urgency, and the frame's white mat as the margin.
WEEKEND_UNIFORM_CSS = """
.hero{padding:0 14u 0 10u}
.weekend-art .hero-top{margin:0 -14u 0 -10u}
.weekend-art .hero-top>.state,.weekend-art .stale{margin-left:10u;margin-right:14u}
.weekend-art .state,.weekend-art .messages h3,.weekend-art .wk-list b,.weather .state{color:@INK@}
.weekend-art h1{margin:3u 14u 10u 10u}
.weekend-art .wk-list,.weekend-art .wk-none{margin-left:10u;margin-right:14u}
.wk-list li{grid-template-columns:52u minmax(0,1fr)}
.wk-list b{letter-spacing:0;text-transform:none;font-size:14u}
.wk-list span{font:15u/1.4 var(--sans)}
.weekend-art .messages{display:grid;gap:3u;margin:0 4u 0 0}
.weekend-art .pb-total{margin:2u 0 0;font-weight:700;font-size:15u;display:flex;align-items:center;gap:7u}
.weather{padding:22u 10u 14u 14u}
.weather .pb-status{margin-top:4u}
.wx-arc text.label{fill:@INK@}
.wx-arc .mark{fill:none}
"""


def render_html(data: dict[str, Any], *, live_messages=None, now: datetime | None = None) -> str:
    validate(data)
    display_now = now
    if display_now is None and data.get("freshness", {}).get("composed_at"):
        display_now = timestamp(data["freshness"]["composed_at"])
    display_now = display_now or datetime.now(timezone.utc)
    if display_now.tzinfo is None:
        raise ValueError("HTML display time requires a timezone")
    if data.get("day_view"):
        from skoleklar.day_view_render import DAY_VIEW_CSS, body_html
        from skoleklar.parents_board_render import SHARED_CSS
        return (
            HTML_TEMPLATE
            .replace("{{CSS}}", _dashboard_css(WEEKEND_UNIFORM_CSS + SHARED_CSS + DAY_VIEW_CSS))
            .replace("{{SCRIPT}}", FOLD_SCRIPT)
            .replace("{{BODY}}", body_html(data))
            .replace('<main class="dashboard">',
                     f'<main class="dashboard day-view" data-now="{esc(display_now.isoformat())}">')
        )
    if data["schema_version"] == 2 and not data.get("weather"):
        from skoleklar.parents_board_render import body_html as parents_body
        body, css = parents_body(data, live_messages)
        return (
            HTML_TEMPLATE
            .replace("{{CSS}}", _dashboard_css(css))
            .replace("{{SCRIPT}}", FOLD_SCRIPT)
            .replace("{{BODY}}", body)
            .replace('<main class="dashboard">',
                     f'<main class="dashboard parents" data-now="{esc(display_now.isoformat())}">')
        )
    hero = data["hero"]
    hero_views = _hero_views(data, hero)
    horizon = []
    row_tracks = []
    if data["schema_version"] == 2:
        forecast = day_weather(data)
        for layout in weekday_layout(data, available_height=309):
            day = layout["day"]
            day_label = "<br>".join(esc(part) for part in day["day"].split(maxsplit=1))
            if day.get("date") in forecast:
                day_label += icon_svg(forecast[day["date"]]["icon"], HTML_ICON_COLOURS)
            children = "".join(weekday_child_html(child) for child in day.get("children", []))
            shared = f'<p class="shared">{esc(day["shared"])}</p>' if day.get("shared") else ""
            # Both the full and the folded form are in the page. The Python fold is the
            # baseline (no script); FOLD_SCRIPT refolds by measurement in the browser.
            row_tracks.append("min-content" if layout["folded"] else "auto")
            rank = "" if layout["rank"] is None else f' data-fold-rank="{layout["rank"]}"'
            rank += ' data-past="1"' if layout["past"] else ""
            horizon.append(
                f'<article class="day{" folded" if layout["folded"] else ""}"{rank}>'
                f'<h3>{day_label}</h3><div class="day-grid">{children}</div>{shared}'
                + "".join(f'<p class="folded-cell">{esc(folded_cell(child))}</p>' for child in day.get("children", []))
                + "</article>"
            )
    else:
        for day in data.get("horizon", []):
            row_tracks.append("min-content")
            children = "".join(weekday_child_html(child) for child in day.get("children", []))
            shared = f'<p class="shared">{esc(day["shared"])}</p>' if day.get("shared") else ""
            day_label = "<br>".join(esc(part) for part in day["day"].split(maxsplit=1))
            horizon.append(f'<article class="day"><h3>{day_label}</h3><div class="day-grid">{children}</div>{shared}</article>')
    days_html = (
        f'<div class="days" style="grid-template-rows:{" ".join(row_tracks)}">{"".join(horizon)}</div>'
        if horizon else ""
    )
    weekend = data.get("weekend", [])
    weekend_html = ""
    week_weekend_html = ""
    if weekend:
        legacy_weekend_rows = "".join(f'<li><b>{esc(item["day"])}</b><span>{esc(item["text"])}</span></li>' for item in weekend)
        if data["schema_version"] == 2:
            # One activity per row: its day in the day column, then whose it is and
            # what, across both child columns (as on the panel).
            names = [child["name"] for child in hero["children"]]
            rows = []
            for item in weekend_rows(data):
                prefix, text = weekend_row_parts(item, names)
                whose = f"<b>{esc(prefix)}</b> " if prefix else ""
                rows.append(f'<li><b>{esc(compact_panel_date(item["day"]))}</b>'
                            f'<span>{whose}{esc(text)}</span></li>')
            overflow = weekend_overflow_label(data)
            week_weekend_html = (
                '<section class="week-weekend"><h3>Weekend'
                + (f'<span>{esc(overflow)}</span>' if overflow else "")
                + f'</h3><ul>{"".join(rows)}</ul></section>'
            )
        else:
            weekend_html = f'<section class="weekend"><h3>Weekend</h3><ul>{legacy_weekend_rows}</ul></section>'
    elif data["schema_version"] == 2:
        week_weekend_html = (
            '<section class="week-weekend"><h3>Weekend</h3>'
            '<p>Ingen registrerede aktiviteter</p></section>'
        )
    later = data.get("later", [])
    later_html = ""
    if later:
        later_rows = "".join(f'<li><b>{esc(item["day"])}</b><span>{esc(item["summary"])}</span></li>' for item in later)
        later_html = f'<section class="later"><h3>Derefter</h3><ul>{later_rows}</ul></section>'
    messages = data.get("messages", {})
    if live_messages is not None:
        unread = int(messages.get("unread_count", sum(bool(item.unread) for item in live_messages)))
        by_child = {item.child: item for item in live_messages[:2]}
        rows = []
        for child in hero["children"]:
            index = hero["children"].index(child)
            item = by_child.get(child["name"])
            if item is None and index < len(live_messages):
                item = live_messages[index]
            if item is None:
                continue
            label = " · ".join(x for x in (item.activity, "ulæst" if item.unread else "") if x)
            sender = item.sender + (" (redacted)" if item.status == "redacted" else "")
            subject = f'<span class="subject">{esc(item.text)}</span>' if item.status != "redacted" else ""
            rows.append(f'<li class="live-message"><b class="message-child">{esc(child["name"])}</b><small>{esc(label)}</small><b>{esc(sender)}</b>{subject}</li>')
        message_html = f'<h3>Beskeder · {unread} ulæste{new_suffix(messages)}</h3><ul class="message-grid">' + "".join(rows) + '</ul>'
    elif messages.get("checked") and not is_stale(data):
        rows = []
        for child in hero["children"]:
            item = next((row for row in messages.get("items", []) if child["name"] in row.get("children", [])), None)
            if item is None:
                continue
            state = "ny" if item.get("new") else "ulæst" if item.get("unread") else "set"
            rows.append(f'<li><b class="message-child">{esc(child["name"])}</b><small>{esc(item.get("activity", ""))} · {state}</small><b>{esc(item["participant"])}</b></li>')
        unread = int(messages.get("unread_count", sum(bool(item.get("unread")) for item in messages.get("items", []))))
        message_html = f'<h3>Beskeder · {unread} ulæste{new_suffix(messages)}</h3><ul class="message-grid">{"".join(rows)}</ul>'
    else:
        message_html = '<h3>Beskeder ikke tjekket</h3>'
    if data.get("weather"):
        # At the weekend: each child's latest message and the unread total, as on the
        # parents' board.
        from skoleklar.parents_board_render import message_html as one_message, message_lines
        lines, total = message_lines(data, live_messages)
        from skoleklar.parents_board_render import total_html
        message_html = "".join(one_message(message, name) for name, message in lines[:2]) + total_html(total)
    if is_stale(data) and not data.get("weather"):
        stale_banner = f'<div class="stale">{esc(freshness_label(data))}</div>'
    elif is_stale(data):
        # At the weekend, old data is said once, in bold in the status.
        stale_banner = ""
    else:
        stale_banner = ""
        if not data.get("weather"):
            message_html += f'<p class="updated">{esc(short_freshness(data))}</p>'
    if data.get("weather"):
        # Weekend: activities on the left, weather on the right; no school-week table.
        from skoleklar.parents_board_render import status_html
        hero_top, compact = weekend_hero_html(data), " compact"
        right_panel = weather_html(data["weather"]).replace("</section>", status_html(data) + "</section>", 1)
    else:
        hero_top, compact = hero_views + weekend_html, ""
        right_panel = (
            f'<section class="week"><div class="week-top"><h2>{esc(short_date(data.get("horizon_label", "Skoledage")))}</h2>'
            f'<div class="labels"><span>{esc(hero["children"][0]["name"])}</span>'
            f'<span>{esc(hero["children"][1]["name"])}</span></div>'
            f'{days_html}{later_html}</div>{week_weekend_html}</section>'
        )
    from skoleklar.parents_board_render import SHARED_CSS
    return (
        HTML_TEMPLATE
        .replace("{{CSS}}", _dashboard_css(SHARED_CSS + WEEKEND_UNIFORM_CSS))
        .replace("{{SCRIPT}}", FOLD_SCRIPT)
        .replace("{{BODY}}", (
            f'<section class="hero{" weekend-art" if data.get("weather") else ""}"><div class="hero-top">{stale_banner}{hero_top}'
            f'</div><div class="hero-foot"><section class="messages{compact}">{message_html}</section></div></section>'
            f'{right_panel}'
        ))
        .replace('<main class="dashboard">',
                 f'<main class="dashboard" data-now="{esc(display_now.isoformat())}">')
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--format", choices=["html", "png", "both"], default="both")
    parser.add_argument("--live-subjects", action="store_true",
                        help="Fetch latest subjects in memory; redact sensitive/uncertain text locally")
    parser.add_argument("--weather", choices=["off", "auto", "force"], default="off",
                        help="Fetch the weekend forecast: auto shows it from Friday afternoon "
                             "to Sunday afternoon, force shows the upcoming weekend now")
    parser.add_argument("--dither", choices=DITHER_MODES, default="hybrid",
                        help="Panel conversion: crisp, hybrid text protection, or full dithering")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    validate(data)
    if args.weather != "off":
        from skoleklar.weather import attach_weather
        data = attach_weather(data, datetime.now().astimezone(), force=args.weather == "force")
    live_messages = None
    if args.live_subjects:
        from skoleklar.live_subjects import load_live_messages
        live_messages = load_live_messages([c["name"] for c in data["hero"]["children"]])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    png_path = args.output_dir / "dashboard.png"
    html_path = args.output_dir / "dashboard.html"
    result = {}
    if live_messages is not None:
        result["live_messages"] = {
            status: sum(item.status == status for item in live_messages)
            for status in ("safe", "redacted", "unavailable", "empty")
        }
    if args.format in {"png", "both"}:
        image = render_png(data, live_messages=live_messages, dither_mode=args.dither)
        image.save(png_path, format="PNG", optimize=True)
        png_path.chmod(0o600)
        digest = hashlib.sha256(png_path.read_bytes()).hexdigest()
        hash_path = args.output_dir / "dashboard.sha256"
        hash_path.write_text(f"{digest}  dashboard.png\n", encoding="ascii")
        hash_path.chmod(0o600)
        result.update(png=str(png_path.resolve()), sha256=digest)
    if args.format in {"html", "both"}:
        html_path.write_text(render_html(data, live_messages=live_messages), encoding="utf-8")
        html_path.chmod(0o600)
        result["html"] = str(html_path.resolve())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
