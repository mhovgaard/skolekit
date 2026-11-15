"""Weather glyphs as flat shapes on a 100-unit grid, shared by the PNG and HTML renderers.

The PNG draws them without anti-aliasing, so quantizing to the six-colour panel
palette cannot add stray fringe colours. The HTML emits the same shapes as inline SVG.
Colours are roles ("ink", "edge", "cloud", "sun", ...) that the renderer maps to its palette.
"""

from __future__ import annotations

import math
from typing import Any

Shape = tuple[Any, ...]


def _rays(cx: float, cy: float, inner: float, outer: float, width: float) -> list[Shape]:
    result: list[Shape] = []
    for step in range(8):
        angle = step * math.pi / 4
        result.append(("line", cx + inner * math.cos(angle), cy + inner * math.sin(angle),
                       cx + outer * math.cos(angle), cy + outer * math.sin(angle), width, "sun"))
    return result


def _sun(cx: float = 50, cy: float = 50, radius: float = 19, inner: float = 27,
         outer: float = 43, width: float = 7) -> list[Shape]:
    return [*_rays(cx, cy, inner, outer, width), ("circle", cx, cy, radius, "sun")]


def _half_disc(cx: float, cy: float, radius: float, role: str) -> Shape:
    points = tuple((cx - radius * math.cos(math.pi * step / 12), cy - radius * math.sin(math.pi * step / 12))
                   for step in range(13))
    return ("poly", points, role)


def _cloud(dx: float = 0, dy: float = 0) -> list[Shape]:
    """A cloud body inside an outline, so it reads on paper and on top of a sun."""
    result: list[Shape] = []
    for grow, role in ((5, "edge"), (0, "cloud")):
        for cx, cy, radius in ((32, 62, 15), (52, 50, 20), (71, 62, 13)):
            result.append(("circle", cx + dx, cy + dy, radius + grow, role))
        result.append(("rect", 32 + dx, 62 + dy - grow, 71 + dx, 77 + dy + grow, role))
    return result


def _arrow(up: bool) -> list[Shape]:
    if up:
        return [("line", 50, 16, 50, 36, 7, "ink"), ("poly", ((50, 2), (36, 20), (64, 20)), "ink")]
    return [("line", 50, 4, 50, 24, 7, "ink"), ("poly", ((50, 38), (36, 20), (64, 20)), "ink")]


def _sun_on_horizon(up: bool) -> list[Shape]:
    return [_half_disc(50, 70, 24, "sun"), ("line", 6, 72, 94, 72, 7, "ink"), *_arrow(up)]


SHAPES: dict[str, list[Shape]] = {
    "sun": _sun(),
    "partly": [*_sun(34, 34, 13, 19, 30, 5), *_cloud(8, 10)],
    "cloud": _cloud(0, 2),
    "rain": [*_cloud(0, -10), *(("line", x, 74, x - 6, 92, 7, "rain") for x in (36, 53, 70))],
    "snow": [*_cloud(0, -10), *(("circle", x, y, 5, "rain") for x, y in ((32, 84), (51, 90), (70, 84)))],
    "storm": [*_cloud(0, -10),
              ("poly", ((56, 62), (40, 84), (51, 84), (45, 99), (66, 76), (55, 76)), "sun")],
    "fog": [("line", x0, y, x1, y, 7, "ink") for x0, x1, y in ((22, 78, 30), (10, 90, 46), (22, 78, 62), (32, 68, 78))],
    "bike": [("ring", 24, 70, 18, 6, "ink"), ("ring", 76, 70, 18, 6, "ink"),
             *(("line", x0, y0, x1, y1, 5, "ink") for x0, y0, x1, y1 in (
                 (24, 70, 42, 40), (42, 40, 68, 40), (68, 40, 76, 70), (42, 40, 52, 70),
                 (52, 70, 24, 70), (52, 70, 68, 40), (33, 35, 48, 35), (68, 40, 64, 26),
                 (64, 26, 77, 26)))],
    "house": [("poly", ((4, 52), (50, 12), (96, 52)), "warm"), ("rect", 18, 52, 82, 92, "ink"),
              ("rect", 43, 66, 57, 92, "ground")],
    "beach": [_half_disc(50, 48, 36, "alert"), ("line", 50, 48, 50, 80, 6, "ink"),
              *(("line", x, y0, x + 14, y1, 6, "rain")
                for x, y0, y1 in ((8, 94, 86), (22, 86, 94), (36, 94, 86), (50, 86, 94),
                                  (64, 94, 86), (78, 86, 94)))],
    "tree": [("rect", 44, 68, 56, 94, "ink"), ("poly", ((50, 6), (22, 42), (78, 42)), "leaf"),
             ("poly", ((50, 26), (12, 70), (88, 70)), "leaf")],
    "museum": [("poly", ((4, 38), (50, 8), (96, 38)), "ink"),
               *(("rect", x, 44, x + 11, 78, "ink") for x in (16, 35, 54, 73)),
               ("rect", 6, 82, 94, 94, "ink")],
    "umbrella": [_half_disc(50, 52, 40, "rain"), ("line", 50, 52, 50, 86, 7, "ink"),
                 ("line", 50, 86, 62, 94, 7, "ink"), ("line", 62, 94, 70, 84, 7, "ink")],
    "wind": [("line", 8, 32, 74, 32, 8, "ink"), ("line", 74, 32, 86, 18, 8, "ink"),
             ("line", 8, 52, 92, 52, 8, "ink"),
             ("line", 8, 72, 62, 72, 8, "ink"), ("line", 62, 72, 74, 86, 8, "ink")],
    "frost": [("line", 50 - 40 * math.cos(angle), 50 - 40 * math.sin(angle),
               50 + 40 * math.cos(angle), 50 + 40 * math.sin(angle), 8, "rain")
              for angle in (0, math.pi / 3, 2 * math.pi / 3)],
    "sunrise": _sun_on_horizon(True),
    "sunset": _sun_on_horizon(False),
}
SHAPES["uv"] = SHAPES["sun"]

CELL_ICONS = ("sun", "partly", "cloud", "rain", "snow", "storm", "fog")
FLAG_ICONS = ("umbrella", "wind", "frost", "uv", "storm")
VERDICT_ICONS = {"Cykeldag": "bike", "Udedag": "sun", "Indedag": "house", "Blandet": "partly",
                 "Stranden": "beach", "Skoven": "tree", "Museum": "museum"}


def draw_icon(draw: Any, name: str, x: int, y: int, size: int, colours: dict[str, str]) -> None:
    """Draw one glyph with its top-left corner at (x, y), `size` pixels square."""
    scale = size / 100

    def point(px: float, py: float) -> tuple[int, int]:
        return (round(x + px * scale), round(y + py * scale))

    for shape in SHAPES[name]:
        kind, colour = shape[0], colours[shape[-1]]
        if kind == "circle":
            _, cx, cy, radius, _ = shape
            draw.ellipse((*point(cx - radius, cy - radius), *point(cx + radius, cy + radius)), fill=colour)
        elif kind == "ring":
            _, cx, cy, radius, width, _ = shape
            draw.ellipse((*point(cx - radius, cy - radius), *point(cx + radius, cy + radius)),
                         outline=colour, width=max(1, round(width * scale)))
        elif kind == "rect":
            _, x0, y0, x1, y1, _ = shape
            draw.rectangle((*point(x0, y0), *point(x1, y1)), fill=colour)
        elif kind == "line":
            _, x0, y0, x1, y1, width, _ = shape
            draw.line((*point(x0, y0), *point(x1, y1)), fill=colour, width=max(1, round(width * scale)))
        else:
            draw.polygon([point(px, py) for px, py in shape[1]], fill=colour)


def icon_svg(name: str, colours: dict[str, str], *, css_class: str = "wx-icon") -> str:
    """The same glyph as inline SVG; sized by CSS through `css_class`."""
    def number(value: float) -> str:
        return f"{value:.1f}".rstrip("0").rstrip(".")

    parts = []
    for shape in SHAPES[name]:
        kind, colour = shape[0], colours[shape[-1]]
        if kind == "circle":
            _, cx, cy, radius, _ = shape
            parts.append(f'<circle cx="{number(cx)}" cy="{number(cy)}" r="{number(radius)}" fill="{colour}"/>')
        elif kind == "ring":
            _, cx, cy, radius, width, _ = shape
            parts.append(f'<circle cx="{number(cx)}" cy="{number(cy)}" r="{number(radius - width / 2)}" '
                         f'fill="none" stroke="{colour}" stroke-width="{number(width)}"/>')
        elif kind == "rect":
            _, x0, y0, x1, y1, _ = shape
            parts.append(f'<rect x="{number(x0)}" y="{number(y0)}" width="{number(x1 - x0)}" '
                         f'height="{number(y1 - y0)}" fill="{colour}"/>')
        elif kind == "line":
            _, x0, y0, x1, y1, width, _ = shape
            parts.append(f'<line x1="{number(x0)}" y1="{number(y0)}" x2="{number(x1)}" y2="{number(y1)}" '
                         f'stroke="{colour}" stroke-width="{number(width)}"/>')
        else:
            points = " ".join(f"{number(px)},{number(py)}" for px, py in shape[1])
            parts.append(f'<polygon points="{points}" fill="{colour}"/>')
    return (f'<svg class="{css_class}" viewBox="0 0 100 100" aria-hidden="true" focusable="false">'
            + "".join(parts) + "</svg>")
