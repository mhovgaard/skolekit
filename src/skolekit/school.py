"""One school's SkoleIntra: `school.toml`.

`school.toml` configures site, classes and wording overrides over the locale.
The toolkit ships no school host or household. See docs/architecture.md.
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from skolekit.wording import DANISH, Wording, WordingError, with_overrides

SCHOOL_FILE = "school.toml"
LOCALE = "da"
TIMEZONE = "Europe/Copenhagen"
# How the Danish locale writes a class: level 2 is '2. klasse'.
CLASS_LABEL = "{level}. klasse"

ALLOWED_HOST_SUFFIX = ".skoleintra.dk"
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")
_PORTAL = re.compile(r"[a-z]+")
_CLASS_CODE = re.compile(r"\d{2}")
# What a product reads, which neither the school's nor the profile's file can hold.
_ELSEWHERE = {"meaning": "the household's own config, which the product reads"}


class ProfileError(ValueError):
    """A profile or school file that can't be used as written."""


@dataclass(frozen=True, slots=True)
class School:
    """One school's SkoleIntra: where it lives and how it names its classes."""

    host: str
    portal: str = "parent"
    locale: str = LOCALE
    timezone: str = TIMEZONE
    class_codes: tuple[str, ...] = tuple(f"{level:02d}" for level in range(10))
    class_label: str = CLASS_LABEL
    wording: Wording = DANISH

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"

    def label(self, level: int) -> str:
        """The class as the school writes it: level 2 is '2. klasse'."""
        return self.class_label.format(level=level)

    def digest(self) -> str:
        """Which rules read the sources: recorded with every refresh of the typed tables."""
        document = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(document.encode("utf-8")).hexdigest()[:12]


def _read(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"{path.name}: {error}") from None


def _refuse_unknown(table: dict[str, Any], allowed: set[str], where: str) -> None:
    for key in table:
        if key in _ELSEWHERE:
            raise ProfileError(f"{where}: [{key}] belongs in {_ELSEWHERE[key]}")
        if key not in allowed:
            raise ProfileError(f"{where}: unknown key {key!r}")


def _string(table: dict[str, Any], key: str, where: str) -> str:
    value = table[key]
    if not isinstance(value, str) or not value:
        raise ProfileError(f"{where} {key} must be a non-empty string")
    return value


def parse_school(document: dict[str, Any], base: School | None = None) -> School:
    """`school.toml` over `base`; without a base the file must name the host."""
    _refuse_unknown(document, {"site", "classes", "lessons", "forms", "notices", "weekly_plan"}, SCHOOL_FILE)
    site, classes = document.get("site", {}), document.get("classes", {})
    if not isinstance(site, dict) or not isinstance(classes, dict):
        raise ProfileError(f"{SCHOOL_FILE}: [site] and [classes] must be tables")
    _refuse_unknown(site, {"host", "portal", "locale", "timezone"}, "[site]")
    _refuse_unknown(classes, {"codes", "label", "broad"}, "[classes]")
    values: dict[str, Any] = {}
    if "host" in site:
        host = _string(site, "host", "[site]")
        if not _HOST.fullmatch(host) or not host.endswith(ALLOWED_HOST_SUFFIX):
            raise ProfileError(f"[site] host must be a lower-case host name ending in {ALLOWED_HOST_SUFFIX}")
        values["host"] = host
    if "portal" in site:
        portal = _string(site, "portal", "[site]")
        if not _PORTAL.fullmatch(portal):
            raise ProfileError("[site] portal must be lower-case letters, such as 'parent'")
        values["portal"] = portal
    if "locale" in site and site["locale"] != LOCALE:
        raise ProfileError(f"[site] locale: only {LOCALE!r} ships so far")
    if "timezone" in site and site["timezone"] != TIMEZONE:
        raise ProfileError(f"[site] timezone: the scan still reads its dates in {TIMEZONE}; "
                           "another timezone needs the code to read it first")
    if "codes" in classes:
        codes = classes["codes"]
        if (not isinstance(codes, list) or not codes or len(set(codes)) != len(codes)
                or not all(isinstance(code, str) and _CLASS_CODE.fullmatch(code) for code in codes)):
            raise ProfileError("[classes] codes must be a list of distinct two-digit strings, such as '02'")
        values["class_codes"] = tuple(codes)
    if "label" in classes:
        label = _string(classes, "label", "[classes]")
        try:
            formatted = label.format(level=0)
        except (KeyError, IndexError, ValueError):
            formatted = label
        if "{level}" not in label or formatted == label:
            raise ProfileError("[classes] label must contain {level} and nothing else in braces")
        values["class_label"] = label
    try:
        values["wording"] = with_overrides(base.wording if base is not None else DANISH, document)
    except WordingError as error:
        raise ProfileError(f"{SCHOOL_FILE} {error}") from None
    if base is not None:
        return replace(base, **values)
    if "host" not in values:
        raise ProfileError(f"{SCHOOL_FILE}: [site] host is required")
    return School(**values)



# --- Writing the file ---------------------------------------------------------------------

def _toml(value: Any) -> str:
    # A JSON string or list of strings is also a TOML basic string or array.
    return json.dumps(value, ensure_ascii=False)


def school_toml(school: School) -> str:
    return "\n".join([
        "# One school's SkoleIntra. Its wording is the locale's until a rule here says otherwise;",
        "# see skolekit/locales/da.toml and docs/architecture.md.",
        "[site]",
        f"host = {_toml(school.host)}",
        f"portal = {_toml(school.portal)}",
        f"locale = {_toml(school.locale)}",
        f"timezone = {_toml(school.timezone)}",
        "",
        "[classes]",
        f"codes = {_toml(list(school.class_codes))}",
        f"label = {_toml(school.class_label)}",
        "",
    ])
