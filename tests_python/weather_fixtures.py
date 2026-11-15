"""Synthetic Open-Meteo responses for weather tests and previews."""
from datetime import date, timedelta

SATURDAY = date(2026, 10, 3)
# One letter per hour 06..20: s(un, code 0), p(artly, 2), c(loud, 3), r(ain, 61), t(hunder, 95).
CODES = {"s": 0, "p": 2, "c": 3, "r": 61, "t": 95, "n": 73, "f": 45}


def forecast(*days, sunrise="07:16", sunset="18:40"):
    """Build a response from (hours, low, high, wind) per day; gusts are 1.6 x wind."""
    hourly = {key: [] for key in ("time", "temperature_2m", "precipitation", "weather_code",
                                  "wind_speed_10m", "wind_gusts_10m", "uv_index")}
    daily = {"time": [], "sunrise": [], "sunset": []}
    for offset, spec in enumerate(days):
        hours, low, high, wind = spec[:4]
        uv = spec[4] if len(spec) > 4 else 2
        day = SATURDAY + timedelta(days=offset)
        daily["time"].append(day.isoformat())
        daily["sunrise"].append(f"{day.isoformat()}T{sunrise}")
        daily["sunset"].append(f"{day.isoformat()}T{sunset}")
        for hour in range(24):
            letter = hours[min(max(hour - 6, 0), len(hours) - 1)]
            # Coldest at 06, warmest at 15.
            warmth = max(0.0, 1 - abs(hour - 15) / 9)
            hourly["time"].append(f"{day.isoformat()}T{hour:02d}:00")
            hourly["temperature_2m"].append(round(low + (high - low) * warmth, 1))
            hourly["precipitation"].append({"r": 0.9, "t": 2.5, "n": 0.6}.get(letter, 0.0))
            hourly["weather_code"].append(CODES[letter])
            hourly["wind_speed_10m"].append(float(wind))
            hourly["wind_gusts_10m"].append(round(wind * 1.6, 1))
            hourly["uv_index"].append(float(uv) if 10 <= hour <= 15 else 0.0)
    return {"hourly": hourly, "daily": daily}


SCENARIOS = {
    "Udedag": (("sssssssssssspcc", 9, 18, 4), ("ppssssssssssccc", 8, 17, 5)),
    "Stranden": (("sssssssssssssss", 17, 26, 4, 7), ("sssssssssssssss", 16, 25, 5, 6)),
    "Skoven": (("cccccccccccccss", 7, 12, 5), ("ccccccccpcccccc", 6, 13, 4)),
    "Blandet": (("ccrrccsrrcccrrc", 9, 14, 7), ("ccsscccrrrcccrr", 8, 13, 6)),
    "Museum": (("ccrrrrrrrrrrrrr", 8, 10, 7), ("ccrrrrrrrrrrrcc", 7, 10, 8)),
    "Indedag": (("rrrrrrrtrrrrrrr", 5, 8, 13), ("rrrrrrrrrrrrrcc", 4, 7, 11)),
    "Cykeldag": (("ccsssssssssscc c".replace(" ", ""), 9, 17, 4, 5), ("ccccsssccrrrrrr", 10, 14, 9)),
}
