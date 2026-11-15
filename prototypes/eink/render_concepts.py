"""Render reproducible e-ink dashboard concepts using Pillow only."""

from __future__ import annotations

import argparse
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont


WIDTH = 800
HEIGHT = 480

# A quiet approximation of the physical panel colours. The final device adapter
# will pass this RGB image to Inky's own palette conversion.
PAPER = "#F6F1E5"
INK = "#252A2B"
MUTED = "#6D716B"
RULE = "#C9C1B2"
WHITE = "#FFFDF7"
BLUE = "#5E7891"
BLUE_PALE = "#DDE4E7"
GREEN = "#687B64"
GREEN_PALE = "#DDE3D8"
YELLOW = "#C9A542"
YELLOW_PALE = "#EEE2B9"
ORANGE = "#B66C43"
ORANGE_PALE = "#ECD6C7"
RED = "#A74E45"

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "output"


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ["Arial Bold.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["Arial.ttf", "DejaVuSans.ttf"]
    )
    roots = [
        Path("/System/Library/Fonts/Supplemental"),
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/local/share/fonts"),
    ]
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


FONTS = {
    "eyebrow": font(12, bold=True),
    "title": font(28, bold=True),
    "subtitle": font(14),
    "day": font(17, bold=True),
    "date": font(12),
    "child": font(14, bold=True),
    "body": font(13),
    "body_bold": font(13, bold=True),
    "small": font(11),
    "small_bold": font(11, bold=True),
    "micro": font(10),
    "micro_bold": font(10, bold=True),
    "hero": font(34, bold=True),
}


DAYS = [
    ("MAN", "14"),
    ("TIR", "15"),
    ("ONS", "16"),
    ("TOR", "17"),
    ("FRE", "18"),
]

WEEK = {
    "Dagny": [
        [("Dansk · læs 20 min", "work"), ("Matematik · ark 4", "work")],
        [("Idræt · løbesko", "bring"), ("Kor · 13.30", "event")],
        [("Matematik · s. 18", "work"), ("Engelsk · 10 ord", "work"), ("Dansk · læs kap. 3", "work"), ("Bibliotek · 11.00", "event")],
        [("Turdag · 9–13", "event"), ("Husk madpakke", "bring")],
        [("Dansk · boganmeldelse", "work")],
    ],
    "Noah": [
        [("Matematik · s. 42–44", "work"), ("Læsning · 15 min", "work")],
        [("Tandpleje · 10.20", "event")],
        [("Engelsk · øv 10 ord", "work"), ("Naturfag · læs s. 7", "work"), ("Dansk · skriv 5 linjer", "work"), ("Gymnastik · 12.00", "event")],
        [("Madpakke til tur", "bring"), ("Svømning · 8.15", "event")],
        [("Aflever boganmeldelse", "attention"), ("Klassemøde · 10.00", "event")],
    ],
}


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    return image, ImageDraw.Draw(image)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, style: str, fill: str = INK) -> None:
    draw.text(xy, value, font=FONTS[style], fill=fill)


def wrapped(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], value: str, style: str,
            fill: str = INK, max_lines: int = 2, spacing: int = 3) -> None:
    x1, y1, x2, _ = box
    selected = FONTS[style]
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=selected)[2] <= x2 - x1:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    lines = lines[:max_lines]
    if len(lines) == max_lines and " ".join(lines) != value:
        while lines[-1] and draw.textbbox((0, 0), lines[-1] + "…", font=selected)[2] > x2 - x1:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    draw.multiline_text((x1, y1), "\n".join(lines), font=selected, fill=fill, spacing=spacing)


def fitted_line(draw: ImageDraw.ImageDraw, xy: tuple[int, int], width: int, value: str,
                style: str = "micro_bold", fill: str = INK) -> None:
    """Draw one compact line, truncating deterministically when needed."""
    selected = FONTS[style]
    rendered = value
    while rendered and draw.textbbox((0, 0), rendered, font=selected)[2] > width:
        rendered = rendered[:-1]
    if rendered != value:
        while rendered and draw.textbbox((0, 0), rendered + "…", font=selected)[2] > width:
            rendered = rendered[:-1]
        rendered += "…"
    draw.text(xy, rendered, font=selected, fill=fill)


def header(draw: ImageDraw.ImageDraw, concept: str, title_value: str = "Ugen i skolen") -> None:
    text(draw, (24, 16), "AG SKOLEOVERBLIK", "eyebrow", BLUE)
    text(draw, (24, 31), title_value, "title")
    text(draw, (620, 22), "14.–18. SEPTEMBER", "small_bold", MUTED)
    text(draw, (646, 41), concept, "small", MUTED)
    draw.line((24, 70, 776, 70), fill=INK, width=2)


def category_colour(category: str) -> tuple[str, str]:
    return {
        "work": (BLUE, BLUE_PALE),
        "event": (GREEN, GREEN_PALE),
        "bring": (YELLOW, YELLOW_PALE),
        "attention": (RED, ORANGE_PALE),
    }[category]


def render_weekly_grid() -> Image.Image:
    image, draw = canvas()
    header(draw, "OPDATERET 06.12")

    left = 72
    top = 82
    grid_right = 776
    col_w = (grid_right - left) / 5
    header_h = 36
    row_h = 108

    for index, (day, date) in enumerate(DAYS):
        x1 = round(left + index * col_w)
        x2 = round(left + (index + 1) * col_w)
        if index == 0:
            draw.rounded_rectangle((x1 + 3, top, x2 - 3, top + header_h - 3), 8, fill=INK)
            text(draw, (x1 + 12, top + 7), day, "day", PAPER)
            text(draw, (x2 - 28, top + 9), date, "date", PAPER)
        else:
            text(draw, (x1 + 10, top + 7), day, "day")
            text(draw, (x2 - 26, top + 9), date, "date", MUTED)

    child_colours = {"Dagny": BLUE, "Noah": GREEN}
    for row, (child, values) in enumerate(WEEK.items()):
        y1 = top + header_h + row * row_h
        y2 = y1 + row_h
        draw.rounded_rectangle((10, y1 + 8, 64, y1 + 35), 12, fill=child_colours[child])
        text(draw, (19, y1 + 13), child, "small_bold", WHITE)
        for index, items in enumerate(values):
            x1 = round(left + index * col_w)
            x2 = round(left + (index + 1) * col_w)
            draw.rectangle((x1, y1, x2, y2), fill=WHITE, outline=RULE, width=1)
            if not items:
                text(draw, (x1 + 11, y1 + 16), "—", "body", RULE)
                continue
            for item_index, (value, category) in enumerate(items[:4]):
                item_y = y1 + 7 + item_index * 24
                accent, pale = category_colour(category)
                draw.rounded_rectangle((x1 + 7, item_y, x2 - 7, item_y + 20), 5, fill=pale)
                draw.ellipse((x1 + 13, item_y + 6, x1 + 21, item_y + 14), fill=accent)
                fitted_line(draw, (x1 + 27, item_y + 4), int(col_w) - 39, value)

    bottom_y = top + header_h + 2 * row_h + 10
    draw.rounded_rectangle((16, bottom_y, 490, 463), 10, fill=WHITE, outline=RULE)
    text(draw, (32, bottom_y + 14), "FÆLLES", "eyebrow", GREEN)
    text(draw, (32, bottom_y + 34), "Fotografering onsdag", "body_bold")
    text(draw, (216, bottom_y + 34), "·", "body", RULE)
    text(draw, (232, bottom_y + 34), "Skolefest fredag kl. 17", "body_bold")
    text(draw, (32, bottom_y + 62), "Ingen nye ændringer i skemaet", "small", MUTED)

    draw.rounded_rectangle((504, bottom_y, 776, 463), 10, fill=ORANGE_PALE)
    text(draw, (520, bottom_y + 12), "PRIVATE BESKEDER · 2 NYE", "eyebrow", ORANGE)
    text(draw, (520, bottom_y + 34), "Anne M. · klasselærer", "small_bold")
    text(draw, (714, bottom_y + 34), "07.48", "small", MUTED)
    text(draw, (520, bottom_y + 54), "Jonas P. · SFO", "small_bold")
    text(draw, (727, bottom_y + 54), "i går", "small", MUTED)
    text(draw, (520, bottom_y + 78), "Åbn SkoleIntra på telefonen", "micro", MUTED)
    return image


def render_today_first() -> Image.Image:
    image, draw = canvas()
    header(draw, "OPDATERET 06.12", "I dag og næste")

    # Large today/tomorrow cards.
    cards = [
        (16, 84, 388, 306, "I DAG · MAN 14", INK, [
            ("Dagny", "Dansk · læs 20 min", "work"),
            ("Noah", "Matematik · s. 42–44", "work"),
        ]),
        (402, 84, 776, 306, "I MORGEN · TIR 15", BLUE, [
            ("Dagny", "Idræt · husk løbesko", "bring"),
            ("Noah", "Ingen forberedelse", "event"),
        ]),
    ]
    for x1, y1, x2, y2, label, accent, items in cards:
        draw.rounded_rectangle((x1, y1, x2, y2), 12, fill=WHITE, outline=RULE)
        draw.rounded_rectangle((x1, y1, x2, y1 + 43), 12, fill=accent)
        draw.rectangle((x1, y1 + 28, x2, y1 + 43), fill=accent)
        text(draw, (x1 + 18, y1 + 14), label, "small_bold", PAPER)
        for index, (child, value, category) in enumerate(items):
            item_y = y1 + 61 + index * 72
            colour, pale = category_colour(category)
            draw.ellipse((x1 + 18, item_y + 4, x1 + 31, item_y + 17), fill=colour)
            text(draw, (x1 + 42, item_y), child, "child")
            wrapped(draw, (x1 + 42, item_y + 23, x2 - 18, item_y + 61), value, "body", MUTED)

    text(draw, (16, 326), "RESTEN AF UGEN", "eyebrow", MUTED)
    horizon = [
        ("ONS 16", "2 lektier", BLUE_PALE, BLUE),
        ("TOR 17", "Turdag", GREEN_PALE, GREEN),
        ("FRE 18", "1 aflevering", ORANGE_PALE, RED),
    ]
    for index, (day, summary, pale, accent) in enumerate(horizon):
        x1 = 16 + index * 188
        draw.rounded_rectangle((x1, 347, x1 + 176, 452), 9, fill=pale)
        text(draw, (x1 + 14, 360), day, "small_bold", accent)
        wrapped(draw, (x1 + 14, 386, x1 + 160, 432), summary, "body_bold")

    draw.rounded_rectangle((590, 326, 776, 452), 10, fill=YELLOW_PALE)
    text(draw, (606, 341), "BESKEDER", "eyebrow", ORANGE)
    text(draw, (606, 367), "1 ny", "hero", INK)
    text(draw, (607, 415), "Tjek på telefonen", "small", MUTED)
    return image


def render_quiet_week() -> Image.Image:
    image, draw = canvas()
    header(draw, "ALT ER AJOUR", "Et roligt ugeblik")

    left = 16
    top = 88
    col_w = 150
    for index, (day, date) in enumerate(DAYS):
        x = left + index * col_w
        if index == 0:
            draw.rounded_rectangle((x, top, x + 138, top + 39), 8, fill=INK)
            text(draw, (x + 14, top + 10), f"{day} {date}", "small_bold", PAPER)
        else:
            text(draw, (x + 8, top + 10), f"{day} {date}", "small_bold", MUTED)

    for row, child in enumerate(("Dagny", "Noah")):
        y = 146 + row * 105
        text(draw, (16, y), child.upper(), "eyebrow", BLUE if row == 0 else GREEN)
        for index, items in enumerate(WEEK[child]):
            x = left + index * col_w
            draw.line((x, y + 25, x + 138, y + 25), fill=RULE)
            if items:
                value, category = items[0]
                colour, _ = category_colour(category)
                draw.ellipse((x, y + 39, x + 9, y + 48), fill=colour)
                wrapped(draw, (x + 17, y + 35, x + 136, y + 81), value, "small_bold", max_lines=2)
            else:
                text(draw, (x + 2, y + 38), "Fri", "small", RULE)

    draw.rounded_rectangle((16, 370, 776, 452), 10, fill=WHITE, outline=RULE)
    draw.rectangle((16, 370, 23, 452), fill=ORANGE)
    text(draw, (39, 386), "ET LILLE TJEK", "eyebrow", ORANGE)
    text(draw, (39, 408), "Ny fællesbesked fra skolen", "body_bold")
    text(draw, (550, 399), "Se den på telefonen  →", "small_bold", MUTED)
    return image


def render_all(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    concepts = {
        "concept-1-weekly-grid.png": render_weekly_grid(),
        "concept-2-today-first.png": render_today_first(),
        "concept-3-quiet-week.png": render_quiet_week(),
    }
    paths = []
    for filename, image in concepts.items():
        path = output_dir / filename
        image.save(path, format="PNG", optimize=True)
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    for path in render_all(args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
