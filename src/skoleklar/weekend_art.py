"""Cached seasonal house artwork selected from the displayed day's forecast.

The packaged house is invented. A household may keep pictures of its own in its
private folder (`artwork/<preset>.webp`); they are never part of the repository.
"""
from __future__ import annotations

import base64
from datetime import date
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

ART_SIZE = (340, 200)
ART_ORIGIN = (0, 82)
SOURCE_SIZE = (680, 400)
ARTWORK_FOLDER = "artwork"
MAX_ARTWORK_BYTES = 2_000_000
PRESETS = tuple(f"{season}-{weather}" for season in ("spring", "summer", "autumn", "winter")
                for weather in ("dry", "rain")) + ("winter-snow",)


def select_artwork(day: dict) -> str:
    """Use the cover's 09–16 weather, rather than evening rain or the other day."""
    month = date.fromisoformat(day["date"]).month
    season = ("winter" if month in (12, 1, 2) else "spring" if month <= 5
              else "summer" if month <= 8 else "autumn")
    if day["icon"] == "snow":
        return "winter-snow"
    condition = "rain" if day["icon"] in ("rain", "storm") else "dry"
    return f"{season}-{condition}"


def household_artwork(preset: str) -> Path | None:
    """The household's own picture for `preset`, if it has put one beside its other files.

    `artwork/<preset>.webp` in the household's private folder replaces the packaged
    house, preset by preset. It must be a WebP of the packaged size; anything else is
    passed over, so a bad file gives the packaged house and never a broken board.
    """
    if preset not in PRESETS:
        raise ValueError("Unknown weekend artwork preset")
    from skolekit.state import state_dir

    path = state_dir() / ARTWORK_FOLDER / f"{preset}.webp"
    try:
        if not path.is_file() or path.stat().st_size > MAX_ARTWORK_BYTES:
            return None
        from PIL import Image

        with Image.open(path) as image:
            return path if image.format == "WEBP" and image.size == SOURCE_SIZE else None
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=2 * len(PRESETS))
def _bytes(preset: str, own: Path | None, _stamp: int) -> bytes:
    if own is not None:
        return own.read_bytes()
    return files("skoleklar").joinpath("artwork", preset + ".webp").read_bytes()


def _source(preset: str) -> tuple[str, Path | None, int]:
    own = household_artwork(preset)
    return preset, own, own.stat().st_mtime_ns if own else 0


def artwork_bytes(preset: str) -> bytes:
    return _bytes(*_source(preset))


def artwork_data_uri(preset: str) -> str:
    return _data_uri(*_source(preset))


def panel_artwork(preset: str):
    """Diffuse only the illustration into all six physical panel pigments."""
    return _panel(*_source(preset))


@lru_cache(maxsize=2 * len(PRESETS))
def _data_uri(preset: str, own: Path | None, stamp: int) -> str:
    return "data:image/webp;base64," + base64.b64encode(_bytes(preset, own, stamp)).decode("ascii")


@lru_cache(maxsize=2 * len(PRESETS))
def _panel(preset: str, own: Path | None, stamp: int):
    import io
    from PIL import Image
    from skoleklar.dashboard_renderer import PANEL_APPEARANCE_RGB, panel_palette

    with Image.open(io.BytesIO(_bytes(preset, own, stamp))) as source:
        fitted = source.convert("RGB").resize(ART_SIZE, Image.Resampling.LANCZOS)
    pixels = fitted.load()
    paper = PANEL_APPEARANCE_RGB[1]
    # Fade the joining edge into paper, matching the browser's image mask.
    for y in range(20):
        alpha = y / 19
        for x in range(ART_SIZE[0]):
            pixels[x, y] = tuple(round(a * alpha + b * (1 - alpha))
                                 for a, b in zip(pixels[x, y], paper))
    palette = list(PANEL_APPEARANCE_RGB.items())
    neutral = [item for item in palette if item[0] in (0, 1)]
    width, height = fitted.size
    output = bytearray(width * height)
    current = [[0.0] * 3 for _ in range(width + 2)]
    for y in range(height):
        following = [[0.0] * 3 for _ in range(width + 2)]
        direction = -1 if y % 2 else 1
        positions = range(width - 1, -1, -1) if direction == -1 else range(width)
        for x in positions:
            original = pixels[x, y]
            adjusted = [original[c] + current[x + 1][c] for c in range(3)]
            candidates = neutral if max(original) - min(original) <= 32 else palette
            code, pigment = min(candidates, key=lambda item: sum(
                (adjusted[c] - item[1][c]) ** 2 for c in range(3)))
            output[y * width + x] = code
            error = [adjusted[c] - pigment[c] for c in range(3)]
            for row, offset, weight in ((current, direction, 7 / 16),
                                        (following, -direction, 3 / 16),
                                        (following, 0, 5 / 16),
                                        (following, direction, 1 / 16)):
                for c in range(3):
                    row[x + 1 + offset][c] += error[c] * weight
        current = following
    result = Image.frombytes("P", ART_SIZE, bytes(output))
    result.putpalette(panel_palette())
    return result
