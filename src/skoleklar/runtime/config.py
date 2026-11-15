"""Where and how the household runs: `[runtime]`, `[weather]` and `[gcp]` in skoleklar.toml.

The household's private file holds runtime, model, scan, weather and cloud
settings beside `[meaning]`:

```toml
[runtime]
where = "gcp"                        # "local" or "gcp": the one place that writes the database
model = "openai/gpt-5.6-terra"       # optional
scans = ["mon-fri 05:45", "12:30", "19:30"]   # optional; these are the defaults

[weather]                            # optional; without it the board shows no forecast
place = "Byen"
latitude = 55.68                     # two decimals, about a kilometre
longitude = 12.57

[gcp]                                # what the GCP skill reads
project = "my-household-project"
region = "europe-west1"
owners = ["parent@example.com"]
viewers = []
```

The timezone is the school's (`school.toml`). Every table is optional; a file
that names an unknown table or key stops before any work.

"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skolekit.profile import ProfileError
from skoleklar.meaning import MEANING_FILE

WHERE = ("local", "gcp")
DEFAULT_SCANS = ("mon-fri 05:45", "12:30", "19:30")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SCAN = re.compile(r"(?:(?P<first>[a-z]{3})(?:-(?P<last>[a-z]{3}))? )?(?P<hour>\d{2}):(?P<minute>\d{2})")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class ScanTime:
    """One scan a day at `hour`:`minute`, on `days` (0 = Monday)."""
    hour: int
    minute: int
    days: tuple[int, ...] = tuple(range(7))

    @property
    def every_day(self) -> bool:
        return self.days == tuple(range(7))

    def cron(self) -> str:
        """Cloud Scheduler's form, as `create-schedules.sh` passes it."""
        if self.every_day:
            weekdays = "*"
        else:
            # cron counts Sunday as 0, so a run that reaches Sunday becomes a list.
            numbers = [(day + 1) % 7 for day in self.days]
            if len(numbers) == 1:
                weekdays = str(numbers[0])
            elif numbers == list(range(numbers[0], numbers[-1] + 1)):
                weekdays = f"{numbers[0]}-{numbers[-1]}"
            else:
                weekdays = ",".join(str(number) for number in sorted(numbers))
        return f"{self.minute} {self.hour} * * {weekdays}"

    def text(self) -> str:
        time = f"{self.hour:02d}:{self.minute:02d}"
        if self.every_day:
            return time
        first, last = DAYS[self.days[0]], DAYS[self.days[-1]]
        return f"{first}-{last} {time}" if first != last else f"{first} {time}"


@dataclass(frozen=True)
class Weather:
    place: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Gcp:
    project: str | None = None
    region: str = "europe-west1"
    owners: tuple[str, ...] = ()
    viewers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeConfig:
    where: str | None = None
    model: str | None = None
    scans: tuple[ScanTime, ...] = field(default_factory=lambda: tuple(parse_scan(s) for s in DEFAULT_SCANS))
    weather: Weather | None = None
    gcp: Gcp = field(default_factory=Gcp)

    def document(self) -> dict[str, Any]:
        """The settings as JSON, for `skoleklar runtime show --json` and the deploy scripts."""
        return {
            "where": self.where,
            "model": self.model,
            "scans": [{"text": scan.text(), "cron": scan.cron(), "hour": scan.hour, "minute": scan.minute,
                       "days": list(scan.days)} for scan in self.scans],
            "weather": None if self.weather is None else {
                "place": self.weather.place, "latitude": self.weather.latitude,
                "longitude": self.weather.longitude},
            "gcp": {"project": self.gcp.project, "region": self.gcp.region,
                    "owners": list(self.gcp.owners), "viewers": list(self.gcp.viewers)},
        }


def parse_scan(text: str) -> ScanTime:
    """`"12:30"` every day, `"mon-fri 05:45"` on a run of days, `"sat 09:00"` on one."""
    match = _SCAN.fullmatch(str(text).strip().lower())
    if not match:
        raise ProfileError(f"{MEANING_FILE}: [runtime] scans: {text!r} is not like 'mon-fri 05:45' or '12:30'")
    hour, minute = int(match["hour"]), int(match["minute"])
    if hour > 23 or minute > 59:
        raise ProfileError(f"{MEANING_FILE}: [runtime] scans: {text!r} is not a time of day")
    if match["first"] is None:
        return ScanTime(hour, minute)
    if match["first"] not in DAYS or (match["last"] and match["last"] not in DAYS):
        raise ProfileError(f"{MEANING_FILE}: [runtime] scans: {text!r} names an unknown day")
    first = DAYS.index(match["first"])
    last = DAYS.index(match["last"]) if match["last"] else first
    if last < first:
        raise ProfileError(f"{MEANING_FILE}: [runtime] scans: {text!r} runs backwards; write 'mon-fri'")
    return ScanTime(hour, minute, tuple(range(first, last + 1)))


def _table(document: dict[str, Any], name: str, keys: set[str]) -> dict[str, Any]:
    table = document.get(name, {})
    if not isinstance(table, dict):
        raise ProfileError(f"{MEANING_FILE}: [{name}] must be a table")
    unknown = set(table) - keys
    if unknown:
        raise ProfileError(f"{MEANING_FILE}: [{name}] unknown key {min(unknown)!r}")
    return table


def parse_runtime(document: dict[str, Any]) -> RuntimeConfig:
    runtime = _table(document, "runtime", {"where", "model", "scans"})
    where = runtime.get("where")
    if where is not None and where not in WHERE:
        raise ProfileError(f"{MEANING_FILE}: [runtime] where is 'local' or 'gcp', not {where!r}")
    model = runtime.get("model")
    if model is not None and (not isinstance(model, str) or not re.fullmatch(r"[\w.-]+/[\w.:-]+", model)):
        raise ProfileError(f"{MEANING_FILE}: [runtime] model is a provider/model name")
    scans = runtime.get("scans", list(DEFAULT_SCANS))
    if not isinstance(scans, list) or not scans or len(scans) > 12:
        raise ProfileError(f"{MEANING_FILE}: [runtime] scans is a list of 1 to 12 times")
    weather_table = _table(document, "weather", {"place", "latitude", "longitude"})
    weather = None
    if weather_table:
        try:
            place = str(weather_table["place"]).strip()
            latitude = round(float(weather_table["latitude"]), 2)
            longitude = round(float(weather_table["longitude"]), 2)
        except (KeyError, TypeError, ValueError):
            raise ProfileError(f"{MEANING_FILE}: [weather] needs place, latitude and longitude") from None
        if not 1 <= len(place) <= 24 or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ProfileError(f"{MEANING_FILE}: [weather] place is 1-24 characters, coordinates in range")
        weather = Weather(place, latitude, longitude)
    gcp_table = _table(document, "gcp", {"project", "region", "owners", "viewers"})
    for key in ("owners", "viewers"):
        values = gcp_table.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and _EMAIL.fullmatch(v) for v in values):
            raise ProfileError(f"{MEANING_FILE}: [gcp] {key} is a list of email addresses")
    project = gcp_table.get("project")
    if project is not None and not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", str(project)):
        raise ProfileError(f"{MEANING_FILE}: [gcp] project is not a Google Cloud project ID")
    region = gcp_table.get("region", "europe-west1")
    if not re.fullmatch(r"[a-z]+-[a-z]+\d", str(region)):
        raise ProfileError(f"{MEANING_FILE}: [gcp] region is not a Google Cloud region")
    gcp = Gcp(project, region, tuple(v.lower() for v in gcp_table.get("owners", [])),
              tuple(v.lower() for v in gcp_table.get("viewers", [])))
    return RuntimeConfig(where, model, tuple(parse_scan(s) for s in scans), weather, gcp)


def load_runtime_config(home: Path) -> RuntimeConfig:
    """The runtime settings in `home`/skoleklar.toml; every default without the file."""
    path = home / MEANING_FILE
    if not path.exists():
        return RuntimeConfig()
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"{MEANING_FILE}: {error}") from None
    return parse_runtime(document)


class WrongWriter(RuntimeError):
    """The household's config names another place as the one writer."""


def require_writer(config: RuntimeConfig, here: str) -> None:
    """Stop a scan in `here` while the config says the other place writes the database."""
    if config.where is not None and config.where != here:
        raise WrongWriter(
            f"{MEANING_FILE} says the household's database is written by its {config.where} runtime; "
            f"a {here} scan would make a second writer, so it stops before any work. "
            + ("Read a pulled copy instead (`skoleklar db pull`)." if here == "local" else
               "Pause the schedules, or set [runtime] where = \"gcp\" after the cloud is seeded."))
