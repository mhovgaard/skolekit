"""The real household's identifiers, read from the owner's own files, never from the repository.

The household's school host, children's names and student IDs live only in
`~/.config/ag-skoleintra/school.toml` and `profile.toml` (decision 1); its cloud
project, the parents' addresses and its town live in `skoleklar.toml`. A guard
test reads them there, on the owner's machine, and checks that none appears in
the code or the fixtures. With or without those files, it also refuses a
real-looking email address, a user's home path and a deployed API Gateway
hostname in any file. It reports a file and the kind of identifier, never
the identifier itself. Where the files don't exist the guard has nothing to
compare with and is skipped.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

OWNER_HOME = Path.home() / ".config" / "ag-skoleintra"
# Font files included: compressed bytes can happen to look like an address.
BINARY = {".png", ".webp", ".jpg", ".ttf", ".otf", ".woff", ".woff2", ".pyc", ".db", ".bin", ".gz"}
MONTHS = {"January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"}


def name_pattern(name: str) -> re.Pattern[str]:
    """A name as a whole word. A name that is also a month counts only outside a date:
    not after a day number ("25 …") or a dash ("June–…"), not before a year ("… 2024")."""
    if name in MONTHS:
        return re.compile(rf"(?<!\d )(?<!–)\b{re.escape(name)}\b(?! \d{{4}})")
    return re.compile(rf"\b{re.escape(name)}\b")


def household_patterns() -> list[tuple[str, re.Pattern[str]]] | None:
    """(kind, pattern) for each of the real household's identifiers, or None without its files."""
    try:
        school = tomllib.loads((OWNER_HOME / "school.toml").read_text(encoding="utf-8"))
        profile = tomllib.loads((OWNER_HOME / "profile.toml").read_text(encoding="utf-8"))
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return None
    patterns = [("school host", re.compile(re.escape(school["site"]["host"].split(".")[0]), re.I))]
    for child in profile.get("children", []):
        patterns.append(("child name", name_pattern(child["name"])))
        patterns.append(("student ID", re.compile(rf"\b{int(child['student_id'])}\b")))
    # Other people the prompts' few-shots once named, now slots in triage.toml.
    try:
        slots = tomllib.loads((OWNER_HOME / "triage.toml").read_text(encoding="utf-8")).get("slots", {})
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        slots = {}
    for name in slots.values():
        patterns.append(("other name", name_pattern(str(name))))
    # Where the household runs: its cloud project, the parents' addresses and its town.
    try:
        runtime = tomllib.loads((OWNER_HOME / "skoleklar.toml").read_text(encoding="utf-8"))
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        runtime = {}
    gcp = runtime.get("gcp", {})
    if gcp.get("project"):
        patterns.append(("cloud project", re.compile(rf"(?<![a-z0-9-]){re.escape(gcp['project'])}(?![a-z0-9])")))
    for address in [*gcp.get("owners", []), *gcp.get("viewers", [])]:
        patterns.append(("parent's address", re.compile(re.escape(str(address)), re.I)))
    place = runtime.get("weather", {}).get("place")
    if place:
        patterns.append(("town", name_pattern(str(place))))
    return patterns


# Anyone's, with or without the owner's files: a real address or a home folder is never
# needed in the repository. Invented addresses use an example domain; Google's own
# service accounts are not people.
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
INVENTED_DOMAIN = re.compile(r"(^|\.)example(\.[a-z.]+)?$|\.gserviceaccount\.com$", re.I)
HOME_PATH = re.compile(r"(?:/Users|/home)/(?!me/|you/|name/|user/|pwuser/|app/|runner/)[A-Za-z][A-Za-z0-9._-]*/")
# A deployed gateway's own hostname (NAME-HASH.REGION.gateway.dev) points at one household's
# cloud. Invented ones say "example".
GATEWAY_HOST = re.compile(r"\b(?P<host>[a-z0-9-]+-[a-z0-9]{8}\.[a-z]{2,4}\.gateway\.dev)\b", re.I)


def personal_traces(text: str) -> list[str]:
    """The kinds of personal trace in `text`: a real-looking address, a home path, a gateway."""
    kinds = []
    if any(not INVENTED_DOMAIN.search(match["domain"]) for match in ADDRESS.finditer(text)):
        kinds.append("an email address")
    if HOME_PATH.search(text):
        kinds.append("a home path")
    if any("example" not in match["host"].lower() for match in GATEWAY_HOST.finditer(text)):
        kinds.append("a gateway hostname")
    return kinds


def kinds_in(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    """The kinds of household identifier and personal trace in `text`."""
    return [kind for kind, pattern in patterns if pattern.search(text)] + personal_traces(text)


def offenders(roots: list[Path], *, skip: tuple[Path, ...] = ()) -> list[str]:
    """'path: kind' for every file under `roots` that holds one of the identifiers."""
    patterns = household_patterns() or []
    found = []
    for root in roots:
        for path in sorted(root.rglob("*") if root.is_dir() else [root]):
            if not path.is_file() or path.suffix in BINARY or any(path.is_relative_to(s) for s in skip):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            found += [f"{path}: {kind}" for kind in kinds_in(text, patterns)]
    return sorted(set(found))
