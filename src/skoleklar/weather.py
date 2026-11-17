#!/usr/bin/env python3
"""Weather for the dashboard: one Open-Meteo read and deterministic rules.

At the weekend this is the full weather panel with a verdict; on school days it is
one glyph and a temperature range per day.

The forecast is fetched for a configured place (postcode precision, never an
address), reduced to the small display object the renderers allowlist, and
attached to the composed dashboard state only while the weekend is relevant.
No model is involved: icons, flags, the verdict word and the best window are rules.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_FIELDS = ("temperature_2m", "precipitation", "weather_code", "wind_speed_10m",
                 "wind_gusts_10m", "uv_index")
MODES = ("family", "private")

# The timeline shows 06..21; half-day cells and day figures use the waking day.
FIRST_HOUR, LAST_HOUR = 6, 21
CELLS = (("Formiddag", 8, 12), ("Efterm.", 12, 18))
DAY_START, DAY_END = 8, 20
# The left-hand weekend cover describes the main daytime, independent of refresh time.
COVER_START, COVER_END = 9, 16
# A school day's glyph covers the hours a child is out of the house.
SCHOOL_START, SCHOOL_END = 7, 17
SCHOOL_DAY_LIMIT = 5

# The dashboard switches to its weekend layout from Friday after school until
# Sunday noon; after that the coming school week matters more.
FRIDAY_FROM_HOUR = 15
SATURDAY_UNTIL_HOUR = 18
SUNDAY_UNTIL_HOUR = 12

RAIN_HOUR_MM = 0.2
WINDOW_LENGTHS = (4, 3, 2)
SNOW_CODES = {71, 73, 75, 77, 85, 86}
FOG_CODES = {45, 48}
_DAY_SHORT = ("Man.", "Tir.", "Ons.", "Tor.", "Fre.", "Lør.", "Søn.")
_DAY_LONG = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag")
_MONTHS = ("jan.", "feb.", "mar.", "apr.", "maj", "jun.", "jul.", "aug.", "sep.", "okt.", "nov.", "dec.")


@dataclass(frozen=True)
class WeatherConfig:
    place: str
    latitude: float
    longitude: float
    mode: str = "family"
    timezone: str = "Europe/Copenhagen"


def config_from_environment() -> WeatherConfig | None:
    """Read the configured place; none configured, or `AG_WEATHER=off`, means no weather.

    The code holds no place of its own: the household's place comes from its
    `skoleklar.toml` (`[weather]`, passed on by the runtime) or the deploy's
    environment.
    """
    if os.getenv("AG_WEATHER", "on").strip().casefold() in {"off", "0", "false", "no"}:
        return None
    place = os.getenv("AG_WEATHER_PLACE", "").strip()
    if not place and not os.getenv("AG_WEATHER_LATITUDE") and not os.getenv("AG_WEATHER_LONGITUDE"):
        return None
    if not os.getenv("AG_WEATHER_LATITUDE") or not os.getenv("AG_WEATHER_LONGITUDE"):
        raise ValueError("AG_WEATHER_PLACE needs AG_WEATHER_LATITUDE and AG_WEATHER_LONGITUDE")
    # Two decimals is roughly one kilometre: enough for a forecast, not an address.
    latitude = round(float(os.environ["AG_WEATHER_LATITUDE"]), 2)
    longitude = round(float(os.environ["AG_WEATHER_LONGITUDE"]), 2)
    mode = os.getenv("AG_WEATHER_MODE", "family").strip().casefold()
    if not place or len(place) > 24:
        raise ValueError("AG_WEATHER_PLACE must be 1-24 characters")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("weather coordinates are out of range")
    if mode not in MODES:
        raise ValueError("AG_WEATHER_MODE must be family or private")
    return WeatherConfig(place, latitude, longitude, mode, os.getenv("AG_TIMEZONE", "Europe/Copenhagen"))


def weekend_dates(now: datetime) -> list[date]:
    """The two days to show at `now`; empty outside the weekend.

    Always two days: Saturday and Sunday, then Sunday and Monday once Saturday is
    nearly over, so the panel never shrinks to a single day.
    """
    today = now.date()
    weekday = now.weekday()
    if weekday == 4 and now.hour >= FRIDAY_FROM_HOUR:
        return [today + timedelta(days=1), today + timedelta(days=2)]
    if weekday == 5:
        first = today if now.hour < SATURDAY_UNTIL_HOUR else today + timedelta(days=1)
        return [first, first + timedelta(days=1)]
    if weekday == 6 and now.hour < SUNDAY_UNTIL_HOUR:
        return [today, today + timedelta(days=1)]
    return []


def upcoming_weekend(now: datetime) -> list[date]:
    """Saturday and Sunday of the current or next weekend; Sunday and Monday on a Sunday."""
    if now.weekday() == 6:
        return [now.date(), now.date() + timedelta(days=1)]
    saturday = now.date() + timedelta(days=(5 - now.weekday()) % 7)
    return [saturday, saturday + timedelta(days=1)]


def fetch_forecast(config: WeatherConfig, days: list[date]) -> dict[str, Any]:
    import httpx

    response = httpx.get(API_URL, timeout=15, params={
        "latitude": config.latitude, "longitude": config.longitude,
        "hourly": ",".join(HOURLY_FIELDS), "daily": "sunrise,sunset",
        "timezone": config.timezone, "wind_speed_unit": "ms",
        "start_date": days[0].isoformat(), "end_date": days[-1].isoformat(),
    })
    response.raise_for_status()
    return response.json()


def _hours_by_day(forecast: dict[str, Any]) -> dict[date, dict[int, dict[str, float]]]:
    hourly = forecast["hourly"]
    result: dict[date, dict[int, dict[str, float]]] = {}
    for index, stamp in enumerate(hourly["time"]):
        moment = datetime.fromisoformat(stamp)
        values = [hourly[field][index] for field in HOURLY_FIELDS]
        if any(value is None for value in values):
            continue
        temp, rain, code, wind, gust, uv = values
        result.setdefault(moment.date(), {})[moment.hour] = {
            "temp": float(temp), "rain": float(rain), "code": int(code),
            "wind": float(wind), "gust": float(gust), "uv": float(uv),
        }
    return result


def _sun_times(forecast: dict[str, Any]) -> dict[date, tuple[str, str]]:
    daily = forecast["daily"]
    return {date.fromisoformat(day): (rise[-5:], down[-5:])
            for day, rise, down in zip(daily["time"], daily["sunrise"], daily["sunset"])}


def _clock(value: str) -> float:
    return int(value[:2]) + int(value[3:]) / 60


def _kind(hour: dict[str, float]) -> str:
    """One timeline letter: r(ain or snow), s(un) or c(loud)."""
    if hour["rain"] >= RAIN_HOUR_MM or hour["code"] >= 51:
        return "r"
    return "s" if hour["code"] <= 2 else "c"


def _half_up(value: float) -> int:
    return math.floor(value + 0.5)


def _cell_icon(hours: list[dict[str, float]]) -> str:
    codes = [hour["code"] for hour in hours]
    count = len(hours)
    if any(code >= 95 for code in codes):
        return "storm"
    if sum(code in SNOW_CODES for code in codes) * 3 >= count:
        return "snow"
    if sum(_kind(hour) == "r" for hour in hours) * 3 >= count or sum(hour["rain"] for hour in hours) >= 1:
        return "rain"
    if sum(code in FOG_CODES for code in codes) * 2 >= count:
        return "fog"
    if sum(code <= 1 for code in codes) * 2 >= count:
        return "sun"
    return "partly" if sum(code <= 2 for code in codes) * 2 >= count else "cloud"


def _rain_mm(hours: list[dict[str, float]]) -> int:
    total = sum(hour["rain"] for hour in hours)
    return 0 if total < RAIN_HOUR_MM else max(1, _half_up(total))


def _cover_icon(hours: list[dict[str, float]]) -> str:
    """Prevailing daytime conditions; a short shower must not label the day rainy."""
    codes = [hour["code"] for hour in hours]
    count = len(hours)
    if any(code >= 95 for code in codes):
        return "storm"
    if sum(code in SNOW_CODES for code in codes) * 2 >= count:
        return "snow"
    if sum(_kind(hour) == "r" for hour in hours) * 2 >= count:
        return "rain"
    if sum(code in FOG_CODES for code in codes) * 2 >= count:
        return "fog"
    if sum(code <= 1 for code in codes) * 2 >= count:
        return "sun"
    return "partly" if sum(code <= 2 for code in codes) * 2 >= count else "cloud"


def _temp_range(low: float, high: float) -> str:
    low_i, high_i = _half_up(low), _half_up(high)
    return f"{high_i}°" if low_i == high_i else f"{low_i}–{high_i}°"


def best_window(hours: dict[int, dict[str, float]], sunrise: str, sunset: str) -> dict[str, Any] | None:
    """The full day's sunniest dry stretch: four hours if possible, else three or two."""
    first = max(FIRST_HOUR, math.ceil(_clock(sunrise)))
    last = min(LAST_HOUR, math.floor(_clock(sunset)))
    for length in WINDOW_LENGTHS:
        best = None
        for start in range(first, last - length + 1):
            part = [hours.get(hour) for hour in range(start, start + length)]
            if any(hour is None or _kind(hour) == "r" for hour in part):
                continue
            temps = [hour["temp"] for hour in part]
            wind = max(hour["wind"] for hour in part)
            # Sun first, then warmth and calm; ties go to the earlier start.
            score = (sum(_kind(hour) == "s" for hour in part), sum(temps) / length - wind)
            if best is None or score > best[0]:
                best = (score, start, min(temps), max(temps), wind)
        if best is not None:
            (sun, _), start, low, high, wind = best
            return {"start": start, "end": start + length, "low": low, "high": high,
                    "wind": wind, "sun": sun}
    return None


def _cyclable(window: dict[str, Any] | None) -> bool:
    return bool(window and window["end"] - window["start"] >= 3 and window["wind"] < 10
                and window["low"] >= 5 and window["high"] <= 28)


def _best_day(metrics: list[dict[str, Any]], *, cycling: bool = False) -> dict[str, Any] | None:
    """The day with the longest, then sunniest, window; the earlier day wins a tie."""
    candidates = [item for item in metrics
                  if (_cyclable(item["window"]) if cycling else item["window"])]
    return max(candidates, default=None,
               key=lambda item: (item["window"]["end"] - item["window"]["start"], item["window"]["sun"]))


def _day_label(day: date) -> str:
    return f"{_DAY_SHORT[day.weekday()]} {day.day}. {_MONTHS[day.month - 1]}"


def _build_day(day: date, hours: dict[int, dict[str, float]], sunrise: str,
               sunset: str) -> tuple[dict[str, Any], dict[str, Any]]:
    missing = [hour for hour in range(FIRST_HOUR, LAST_HOUR) if hour not in hours]
    if missing:
        raise ValueError("forecast does not cover the display hours")
    cells = []
    for label, start, end in CELLS:
        part = [hours[hour] for hour in range(start, end)]
        cells.append({"label": label, "icon": _cell_icon(part),
                      "temp": _half_up(max(hour["temp"] for hour in part)), "rain_mm": _rain_mm(part)})
    waking = [hours[hour] for hour in range(DAY_START, DAY_END)]
    wind = max(hour["wind"] for hour in waking)
    rain = _rain_mm(waking)
    low = min(hour["temp"] for hour in waking)
    uv = max(hour["uv"] for hour in waking)
    flags = []
    if any(hour["code"] >= 95 for hour in waking):
        flags.append({"icon": "storm", "label": "Torden"})
    if wind >= 9:
        flags.append({"icon": "wind", "label": f"{_half_up(wind)} m/s"})
    if rain >= 1:
        flags.append({"icon": "umbrella", "label": f"{rain} mm"})
    if low <= 0:
        flags.append({"icon": "frost", "label": "Frost"})
    if uv >= 5:
        flags.append({"icon": "uv", "label": f"UV {_half_up(uv)}"})
    window = best_window(hours, sunrise, sunset)
    cover = [hours[hour] for hour in range(COVER_START, COVER_END)]
    state = {
        "date": day.isoformat(), "day": _day_label(day), "sunrise": sunrise, "sunset": sunset,
        # All cover figures describe 09:00–16:00, not the evening or only future hours.
        "icon": _cover_icon(cover), "low": _half_up(min(hour["temp"] for hour in cover)),
        "high": _half_up(max(hour["temp"] for hour in cover)), "rain_mm": _rain_mm(cover),
        "cells": cells, "flags": flags[:2],
        "hours": "".join(_kind(hours[hour]) for hour in range(FIRST_HOUR, LAST_HOUR)),
        "window": None if window is None else {
            "start": window["start"], "end": window["end"],
            "label": f"Tør · {_temp_range(window['low'], window['high'])}"},
    }
    kinds = [_kind(hour) for hour in waking]
    metrics = {"day": day, "sunset": sunset, "window": window, "kinds": kinds,
               "low": low, "high": max(hour["temp"] for hour in waking),
               "wind": wind, "gust": max(hour["gust"] for hour in waking)}
    return state, metrics


def _span(window: dict[str, Any]) -> str:
    return f"{window['start']}–{window['end']}"


def verdict(metrics: list[dict[str, Any]], mode: str = "family") -> tuple[str, dict[str, Any] | None]:
    """The verdict word and, when one day stands out, that day's metrics."""
    kinds = [kind for item in metrics for kind in item["kinds"]]
    rain = kinds.count("r") / len(kinds)
    sun = kinds.count("s") / len(kinds)
    high = max(item["high"] for item in metrics)
    wind = max(item["wind"] for item in metrics)
    gust = max(item["gust"] for item in metrics)
    cycle_day = _best_day(metrics, cycling=True)
    if mode == "private" and cycle_day is not None:
        return "Cykeldag", cycle_day
    if rain >= 0.7:
        return ("Indedag" if wind >= 10 or gust >= 17 or high <= 5 else "Museum"), None
    if rain <= 0.2:
        if high >= 22 and wind < 9:
            return "Stranden", None
        return ("Udedag" if sun >= 0.4 else "Skoven"), None
    return "Blandet", _best_day(metrics)


def _reason(word: str, focus: dict[str, Any] | None, metrics: list[dict[str, Any]]) -> str:
    low = min(item["low"] for item in metrics)
    high = max(item["high"] for item in metrics)
    temps = _temp_range(low, high)
    if word == "Cykeldag" and focus:
        window = focus["window"]
        return (f"{_DAY_LONG[focus['day'].weekday()]} er dagen: tørt kl. {_span(window)}, "
                f"{_temp_range(window['low'], window['high'])} og lyst til {focus['sunset']}.")
    if word == "Stranden":
        return f"Varmt og stille, op til {_half_up(high)}°. Husk solcreme og badetøj."
    if word == "Udedag":
        return f"Tørt med sol, {temps}. Godt vejr til at være ude."
    if word == "Skoven":
        return f"Gråt men tørt, {temps}. Godt til en tur i skoven."
    if word == "Museum":
        return f"Regn det meste af tiden, {temps}. Godt vejr til museum."
    if word == "Indedag":
        gust = max(item["gust"] for item in metrics)
        if gust >= 12:
            return f"Regn og blæst, vindstød op til {_half_up(gust)} m/s. Bliv inde."
        return f"Regn og kun {_half_up(high)}°. Bliv inde."
    if focus:
        return (f"Skiftende vejr med byger. Bedst "
                f"{_DAY_LONG[focus['day'].weekday()].lower()} kl. {_span(focus['window'])}.")
    return "Skiftende vejr med byger. Ingen lang tør periode."


def build_weather(forecast: dict[str, Any], days: list[date], config: WeatherConfig,
                  now: datetime) -> dict[str, Any]:
    """Reduce a raw forecast to the display object validated by the renderer."""
    by_day = _hours_by_day(forecast)
    sun = _sun_times(forecast)
    states, metrics = [], []
    for day in days:
        if day not in by_day or day not in sun:
            if day.weekday() < 5:
                continue  # Monday only rounds the panel off; the weekend never depends on it.
            raise ValueError("forecast does not cover the weekend")
        # Each displayed day keeps its full-day perspective, even after its dry window.
        state, metric = _build_day(day, by_day[day], *sun[day])
        states.append(state)
        # The verdict and the bike ride are about the weekend, not Monday.
        if day.weekday() >= 5:
            metrics.append(metric)
    word, focus = verdict(metrics, config.mode)
    result = {
        "place": config.place, "verdict": word, "reason": _reason(word, focus, metrics),
        "label": "Bedste cykeltid" if config.mode == "private" else "Bedste tid ude",
        "days": states,
    }
    cycle_day = _best_day(metrics, cycling=True)
    if cycle_day is not None and word != "Cykeldag":
        short = _DAY_SHORT[cycle_day["day"].weekday()].lower()
        result["note"] = f"Cykeltur: {short} {_span(cycle_day['window'])} · lyst til {cycle_day['sunset']}"
    return result


def weekend_weather(now: datetime, *, config: WeatherConfig | None = None,
                    fetch: Callable[[WeatherConfig, list[date]], dict[str, Any]] = fetch_forecast,
                    force: bool = False) -> dict[str, Any] | None:
    """The weather object for `now`, or None when disabled or outside the weekend."""
    config = config or config_from_environment()
    if config is None:
        return None
    now = now.astimezone(ZoneInfo(config.timezone))
    days = weekend_dates(now) or (upcoming_weekend(now) if force else [])
    if not days:
        return None
    return build_weather(fetch(config, days), days, config, now)


def build_school_days(forecast: dict[str, Any], days: list[date]) -> list[dict[str, Any]]:
    """One glyph and temperature range per school day; days the forecast lacks are left out."""
    by_day = _hours_by_day(forecast)
    result = []
    for day in days:
        hours = [by_day.get(day, {}).get(hour) for hour in range(SCHOOL_START, SCHOOL_END)]
        if any(hour is None for hour in hours):
            continue
        result.append({
            "date": day.isoformat(), "icon": _cell_icon(hours),
            "low": _half_up(min(hour["temp"] for hour in hours)),
            "high": _half_up(max(hour["temp"] for hour in hours)),
            "rain_mm": _rain_mm(hours),
        })
    return result


def school_day_weather(state: dict[str, Any], now: datetime, *, config: WeatherConfig | None = None,
                       fetch: Callable[[WeatherConfig, list[date]], dict[str, Any]] = fetch_forecast,
                       ) -> list[dict[str, Any]]:
    """Weather for the state's school days from today on; empty when disabled or all past."""
    config = config or config_from_environment()
    if config is None:
        return []
    today = now.astimezone(ZoneInfo(config.timezone)).date()
    days = sorted(day for day in (date.fromisoformat(item["date"]) for item in state.get("weekdays", []))
                  if day >= today)[:SCHOOL_DAY_LIMIT]
    return build_school_days(fetch(config, days), days) if days else []


def attach_weather(state: dict[str, Any], now: datetime, *, force: bool = False,
                   **options: Any) -> dict[str, Any]:
    """Add `weather` while the weekend panel applies, otherwise `day_weather` for school days."""
    weather = weekend_weather(now, force=force, **options)
    if weather is not None:
        return {**state, "weather": weather}
    days = school_day_weather(state, now, **options)
    return {**state, "day_weather": days} if days else state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", type=datetime.fromisoformat, default=None)
    parser.add_argument("--force", action="store_true",
                        help="Show the upcoming weekend even on a weekday")
    args = parser.parse_args()
    now = args.now or datetime.now(ZoneInfo(os.getenv("AG_TIMEZONE", "Europe/Copenhagen")))
    if now.tzinfo is None:
        parser.error("--now requires a timezone")
    print(json.dumps(weekend_weather(now, force=args.force), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
