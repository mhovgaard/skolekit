"""Who the household is: `profile.toml`, and the school it reads.

`profile.toml` holds the children this login sees; `school.toml` holds the
school. Both are private configuration, required without bundled defaults.
See docs/architecture.md for configuration ownership.
"""
from __future__ import annotations

import re
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from skolekit.school import (
    SCHOOL_FILE,
    ProfileError,
    School,
    _read,
    _refuse_unknown,
    _string,
    _toml,
    parse_school,
    school_toml,
)

PROFILE_FILE = "profile.toml"
CONFIG_FILES = (SCHOOL_FILE, PROFILE_FILE)
_URL_NAME = re.compile(r"[^/\s?#%]+")


@dataclass(frozen=True, slots=True)
class Child:
    """One child as SkoleIntra shows it to this login.

    `child` is the name in the site's URLs and in every record. `calendar_class_name`
    is the class the lessons calendar asks for ('002'). The class level and code
    default to what that name says: level 2, code '02'.
    """

    student_id: int
    child: str
    calendar_class_name: str
    department_ids: tuple[str, ...] = ("0",)
    class_level: int | None = None
    class_code: str | None = None
    school: School = field(kw_only=True)

    def __post_init__(self) -> None:
        level = self.class_level
        if level is None and self.calendar_class_name.isdigit():
            level = int(self.calendar_class_name)
        object.__setattr__(self, "class_level", level)
        if self.class_code is None and level is not None:
            object.__setattr__(self, "class_code", f"{level:02d}")

    def route(self, suffix: str) -> str:
        return (f"{self.school.base_url}/{self.school.portal}/{self.student_id}/"
                f"{self.child}/{suffix.lstrip('/')}")

    def weekly_route(self, suffix: str) -> str:
        return (f"{self.school.base_url}/{self.school.portal}/{self.student_id}/"
                f"{self.child}item/weeklyplansandhomework/{suffix.lstrip('/')}")


@dataclass(frozen=True, slots=True)
class SiteProfile:
    """The household's side: one school and the children this login sees."""

    school: School
    children: tuple[Child, ...]
    # The files that were read; empty means today's defaults.
    sources: tuple[str, ...] = ()

    @property
    def login_url(self) -> str:
        """The page a login lands on, and the page a session check asks for."""
        return self.children[0].route("learningplan/student")

    @property
    def messages_url(self) -> str:
        return self.children[0].route("messages/conversations")

    def child_named(self, name: str) -> Child | None:
        return next((child for child in self.children if child.child == name), None)

    def class_levels(self) -> dict[str, int]:
        return {child.child: child.class_level for child in self.children
                if child.class_level is not None}

    def class_labels(self) -> dict[str, str]:
        return {name: self.school.label(level) for name, level in self.class_levels().items()}


def load_profile(home: Path, *, default: SiteProfile | None = None) -> SiteProfile:
    """The profile from `home`'s files, with `default` for a file that is missing."""
    sources: list[str] = []
    school = default.school if default is not None else None
    if (home / SCHOOL_FILE).exists():
        school = parse_school(_read(home / SCHOOL_FILE), school)
        sources.append(SCHOOL_FILE)
    if school is None:
        raise ProfileError(f"{SCHOOL_FILE} is missing from {home}")
    if (home / PROFILE_FILE).exists():
        children = parse_children(_read(home / PROFILE_FILE), school)
        sources.append(PROFILE_FILE)
    elif default is not None:
        children = tuple(replace(child, school=school) for child in default.children)
        for child in children:
            _check_code(child.class_code, school, f"{PROFILE_FILE} (default) {child.child}")
    else:
        raise ProfileError(f"{PROFILE_FILE} is missing from {home}")
    return SiteProfile(school, children, tuple(sources))


def parse_children(document: dict[str, Any], school: School) -> tuple[Child, ...]:
    _refuse_unknown(document, {"children"}, PROFILE_FILE)
    rows = document.get("children")
    if not isinstance(rows, list) or not rows:
        raise ProfileError(f"{PROFILE_FILE}: list at least one [[children]]")
    children: list[Child] = []
    for number, row in enumerate(rows, start=1):
        where = f"{PROFILE_FILE} child {number}"
        if not isinstance(row, dict):
            raise ProfileError(f"{where}: must be a table")
        _refuse_unknown(row, {"student_id", "name", "calendar_class_name", "class_level",
                              "class_code", "department_ids"}, where)
        for key in ("student_id", "name", "calendar_class_name"):
            if key not in row:
                raise ProfileError(f"{where}: {key} is required")
        student_id = row["student_id"]
        if not isinstance(student_id, int) or isinstance(student_id, bool) or student_id <= 0:
            raise ProfileError(f"{where}: student_id must be a positive whole number")
        name = _string(row, "name", where)
        if not _URL_NAME.fullmatch(name):
            raise ProfileError(f"{where}: name is a part of the site's URLs, so it can't hold "
                               "spaces, '/', '?', '#' or '%'")
        calendar_class_name = _string(row, "calendar_class_name", where)
        level = row.get("class_level")
        if level is not None and (not isinstance(level, int) or isinstance(level, bool)
                                  or not 0 <= level <= 10):
            raise ProfileError(f"{where}: class_level must be a whole number from 0 to 10")
        if level is None and not calendar_class_name.isdigit():
            raise ProfileError(f"{where}: class_level is needed when calendar_class_name "
                               "is not a number")
        code = row.get("class_code")
        if code is not None and not isinstance(code, str):
            raise ProfileError(f"{where}: class_code must be a string")
        departments = row.get("department_ids", ["0"])
        if (not isinstance(departments, list) or not departments
                or not all(isinstance(value, str) and value for value in departments)):
            raise ProfileError(f"{where}: department_ids must be a list of strings")
        child = Child(student_id=student_id, child=name, calendar_class_name=calendar_class_name,
                      department_ids=tuple(departments), class_level=level, class_code=code,
                      school=school)
        if child.class_level is not None and not 0 <= child.class_level <= 10:
            raise ProfileError(f"{where}: the class level must be from 0 to 10")
        _check_code(child.class_code, school, where)
        children.append(child)
    for key, label in ((lambda child: child.student_id, "student_id"),
                       (lambda child: child.child, "name")):
        values = [key(child) for child in children]
        if len(set(values)) != len(values):
            raise ProfileError(f"{PROFILE_FILE}: two children share a {label}")
    return tuple(children)


def _check_code(code: str | None, school: School, where: str) -> None:
    if code is not None and code not in school.class_codes:
        raise ProfileError(f"{where}: class_code {code!r} is not in {SCHOOL_FILE} [classes] codes")


# --- Writing the files ---------------------------------------------------------------------

def profile_toml(children: tuple[Child, ...]) -> str:
    lines = ["# The children this login sees. The first one's page is where a login lands.",
             "# Private: keep this file in the state directory and the private bucket only."]
    for child in children:
        lines += ["", "[[children]]",
                  f"student_id = {child.student_id}",
                  f"name = {_toml(child.child)}",
                  f"calendar_class_name = {_toml(child.calendar_class_name)}"]
        if child.class_level is not None:
            lines.append(f"class_level = {child.class_level}")
        if child.class_code is not None:
            lines.append(f"class_code = {_toml(child.class_code)}")
        lines.append(f"department_ids = {_toml(list(child.department_ids))}")
    return "\n".join(lines) + "\n"


def write_profile(profile: SiteProfile, home: Path, *, force: bool = False) -> list[Path]:
    """Write both files, readable by this user only; refuse to replace one unless forced."""
    targets = {home / SCHOOL_FILE: school_toml(profile.school),
               home / PROFILE_FILE: profile_toml(profile.children)}
    existing = [path.name for path in targets if path.exists()]
    if existing and not force:
        raise ProfileError(f"already present: {', '.join(existing)} (use --force to replace)")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(stat.S_IRWXU)
    for path, text in targets.items():
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary.replace(path)
    if load_profile(home) != replace(profile, sources=CONFIG_FILES):
        raise ProfileError("the written files don't read back as the same profile")
    return list(targets)
