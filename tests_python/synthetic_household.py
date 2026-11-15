"""An invented school and two invented children: the household every test uses.

The product holds no school or children of its own (see docs/architecture.md), so each test's state directory gets
these two files (conftest.py), and a test that builds children uses these.
Never put a real school, child, name or ID here.
"""
from __future__ import annotations

from pathlib import Path

from skolekit.profile import Child, SiteProfile, profile_toml
from skolekit.school import School, school_toml

SCHOOL = School(host="eksempel.m.skoleintra.dk")
ALFA = Child(student_id=9101, child="Alfa", calendar_class_name="001", school=SCHOOL)
BETA = Child(student_id=9202, child="Beta", calendar_class_name="003", school=SCHOOL)
CHILDREN = (ALFA, BETA)
PROFILE = SiteProfile(SCHOOL, CHILDREN)


def write_household(home: Path) -> Path:
    """Write the invented household's school.toml and profile.toml into `home`."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "school.toml").write_text(school_toml(SCHOOL), encoding="utf-8")
    (home / "profile.toml").write_text(profile_toml(CHILDREN), encoding="utf-8")
    return home
