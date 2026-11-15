"""The household's profile.toml and school.toml (phase 1 of the architecture review).

Both files are required: the code holds no school and no children (decision 1).
The school's host, portal and class label and the children come from them.
Invented schools and children only (tests_python/synthetic_household.py).
"""
import argparse
import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from skoleklar.store import typed as typed_sources
from skoleklar import cli, day_summaries, pipeline
from skoleklar.cli import common, lifecycle, messages
from skoleklar.triage import derivation, review_checks
from skoleklar import household as sp
from skolekit.profile import ProfileError, SiteProfile, load_profile, write_profile
from skolekit.auth.session import SessionStore
from skoleklar.storage import SnapshotStore
from tests_python.synthetic_household import PROFILE, SCHOOL

SRC = Path(__file__).resolve().parents[1] / "src"

# The invented school's file with every value spelled out: the locale's defaults.
SPELLED_OUT_SCHOOL = """\
[site]
host = "eksempel.m.skoleintra.dk"
portal = "parent"
locale = "da"
timezone = "Europe/Copenhagen"

[classes]
codes = ["00", "01", "02", "03", "04", "05", "06", "07", "08", "09"]
label = "{level}. klasse"
"""

OTHER_SCHOOL = """\
[site]
host = "eksempelskolen.m.skoleintra.dk"
portal = "foraelder"

[classes]
codes = ["00", "01", "02"]
label = "{level}. årgang"
"""

OTHER_CHILDREN = """\
[[children]]
student_id = 9101
name = "Alfa"
calendar_class_name = "2A"
class_level = 2
department_ids = ["0", "7"]

[[children]]
student_id = 9202
name = "Beta"
calendar_class_name = "001"
"""


def write(home: Path, **files: str) -> Path:
    for name, text in files.items():
        (home / f"{name}.toml").write_text(text, encoding="utf-8")
    return home


# --- Both files, no defaults ---------------------------------------------------------------

def test_without_the_files_nothing_runs(tmp_path):
    with pytest.raises(ProfileError, match="school.toml is missing"):
        sp.load_site_profile(tmp_path)
    write(tmp_path, school=OTHER_SCHOOL)
    with pytest.raises(ProfileError, match="profile.toml is missing"):
        sp.load_site_profile(tmp_path)


def test_the_product_holds_no_school_or_children():
    source = (SRC / "skoleklar" / "household.py").read_text(encoding="utf-8")
    assert "School(" not in source and "Child(" not in source and "DEFAULT" not in source


def test_class_labels_come_from_the_label():
    # Phase 1's bar: the label the model is told is "{level}. klasse" with the child's level.
    assert PROFILE.class_labels() == {"Alfa": "1. klasse", "Beta": "3. klasse"}
    assert [(child.class_level, child.class_code) for child in PROFILE.children] == [(1, "01"), (3, "03")]


def test_the_state_dir_is_ag_school_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    assert sp.state_home() == common.config_root() == tmp_path.resolve()
    write(tmp_path, school=OTHER_SCHOOL, profile=OTHER_CHILDREN)
    assert sp.load_site_profile().school.host == "eksempelskolen.m.skoleintra.dk"


def test_a_spelled_out_school_equals_its_host_alone(tmp_path):
    profile = sp.load_site_profile(write(tmp_path, school=SPELLED_OUT_SCHOOL, profile=OTHER_CHILDREN))
    assert profile.school == SCHOOL
    assert profile.sources == ("school.toml", "profile.toml")


def test_written_files_read_back_as_the_same_profile(tmp_path):
    paths = write_profile(PROFILE, tmp_path / "home")
    assert [path.name for path in paths] == ["school.toml", "profile.toml"]
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
    assert (tmp_path / "home").stat().st_mode & 0o777 == 0o700
    loaded = sp.load_site_profile(tmp_path / "home")
    assert replace(loaded, sources=()) == PROFILE
    with pytest.raises(ProfileError, match="already present"):
        write_profile(PROFILE, tmp_path / "home")
    write_profile(loaded, tmp_path / "home", force=True)


def test_the_product_writes_both_files_from_the_host_and_children(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path / "home"))
    args = cli.build_parser().parse_args(["profile", "write", "--host", SCHOOL.host,
                                          "--child", "Alfa:9101:001", "--child", "Beta:9202:003"])
    assert args.handler(args) == 0
    assert replace(sp.load_site_profile(), sources=()) == PROFILE


# --- Another school ------------------------------------------------------------------------

def test_another_school_and_household(tmp_path):
    profile = sp.load_site_profile(write(tmp_path, school=OTHER_SCHOOL, profile=OTHER_CHILDREN))
    alfa, beta = profile.children
    assert alfa.route("Index") == "https://eksempelskolen.m.skoleintra.dk/foraelder/9101/Alfa/Index"
    assert alfa.weekly_route("list") == (
        "https://eksempelskolen.m.skoleintra.dk/foraelder/9101/Alfaitem/weeklyplansandhomework/list")
    assert profile.login_url.endswith("/foraelder/9101/Alfa/learningplan/student")
    assert profile.messages_url.endswith("/foraelder/9101/Alfa/messages/conversations")
    assert (alfa.class_level, alfa.class_code, alfa.department_ids) == (2, "02", ("0", "7"))
    assert (beta.class_level, beta.class_code, beta.department_ids) == (1, "01", ("0",))
    assert profile.class_labels() == {"Alfa": "2. årgang", "Beta": "1. årgang"}
    assert profile.child_named("Beta") is beta and profile.child_named("Gamma") is None


@pytest.mark.parametrize("name, text, message", [
    ("school", "[site]\nhost = \"example.com\"\n", "ending in .skoleintra.dk"),
    ("school", "[site]\nhost = \"Eksempel.m.skoleintra.dk\"\n", "lower-case host"),
    ("school", "[site]\nportal = \"parent/x\"\n", "portal"),
    ("school", "[site]\nlocale = \"en\"\n", "locale"),
    ("school", "[site]\ntimezone = \"UTC\"\n", "timezone"),
    ("school", "[site]\nhots = \"x\"\n", "unknown key 'hots'"),
    ("school", "[forms]\nbooked = \"Min tid\"\n", "must hold {date}, {time}, once each"),
    ("school", "[forms]\nbooked = '{date} {date} {time}'\n", "must hold {date}, {time}, once each"),
    ("school", "[forms]\nbooked = '({date} {time}'\n", "not a regular expression"),
    ("school", "[forms]\nsignup = 'x {from} {until}'\n", "unknown rule 'signup'"),
    ("school", "[notices]\nminutes = 'varer {date}'\n", "must hold {minutes}"),
    ("school", "[classes]\nbroad = []\n", "list of non-empty strings"),
    ("school", "[weekly_plan.sections]\nhomework = []\n", "list of non-empty strings"),
    ("school", "[weekly_plan.sections]\nlunch = ['Frokost']\n", "unknown kind 'lunch'"),
    ("school", "[weekly_plan.sections]\nbring = ['(Medbring)']\n", "use (?:...)"),
    ("school", "[weekly_plan]\nheadings = []\n", "only [weekly_plan.sections]"),
    ("school", "[meaning.bookings]\ndefault_minutes = 30\n", "belongs in the household's own config"),
    ("school", "[classes]\nlabel = \"klasse\"\n", "{level}"),
    ("school", "[classes]\nlabel = \"{level}. {grade}\"\n", "{level}"),
    ("school", "[classes]\ncodes = [\"2\"]\n", "two-digit"),
    ("school", "[classes]\ncodes = [\"02\", \"02\"]\n", "distinct"),
    ("school", "[classes]\ncodes = [\"05\"]\n", "not in school.toml"),
    ("school", "[site\n", "school.toml"),
    ("profile", "children = []\n", "at least one"),
    ("profile", "[meaning.bookings]\ndefault_minutes = 30\n", "belongs in the household's own config"),
    ("profile", "[[children]]\nstudent_id = 1\ncalendar_class_name = \"001\"\n", "name is required"),
    ("profile", "[[children]]\nstudent_id = 0\nname = \"A\"\ncalendar_class_name = \"001\"\n", "positive"),
    ("profile", "[[children]]\nstudent_id = true\nname = \"A\"\ncalendar_class_name = \"001\"\n", "positive"),
    ("profile", "[[children]]\nstudent_id = 1\nname = \"A B\"\ncalendar_class_name = \"001\"\n", "URLs"),
    ("profile", "[[children]]\nstudent_id = 1\nname = \"A\"\ncalendar_class_name = \"2A\"\n", "class_level is needed"),
    ("profile", "[[children]]\nstudent_id = 1\nname = \"A\"\ncalendar_class_name = \"012\"\n", "0 to 10"),
    ("profile", "[[children]]\nstudent_id = 1\nname = \"A\"\ncalendar_class_name = \"001\"\nclass_level = 11\n",
     "0 to 10"),
    ("profile", "[[children]]\nstudent_id = 1\nname = \"A\"\ncalendar_class_name = \"001\"\nclass_code = \"X1\"\n",
     "not in school.toml"),
    ("profile", "[[children]]\nstudent_id = 1\nname = \"A\"\ncalendar_class_name = \"001\"\ndepartment_ids = [0]\n",
     "department_ids"),
    ("profile", "[[children]]\nstudent_id = 1\nname = \"A\"\ncalendar_class_name = \"001\"\nnickname = \"x\"\n",
     "unknown key 'nickname'"),
    ("profile", "[[children]]\nstudent_id = 1\nname = \"A\"\ncalendar_class_name = \"001\"\n"
                "[[children]]\nstudent_id = 1\nname = \"B\"\ncalendar_class_name = \"001\"\n", "share a student_id"),
    ("profile", "[[children]]\nstudent_id = 1\nname = \"A\"\ncalendar_class_name = \"001\"\n"
                "[[children]]\nstudent_id = 2\nname = \"A\"\ncalendar_class_name = \"001\"\n", "share a name"),
])
def test_a_file_that_cant_be_used_as_written_is_refused(tmp_path, name, text, message):
    # The other file, and the host, come from the invented household, as a product's own would.
    write(tmp_path, **{name: text})
    with pytest.raises(ProfileError, match=re.escape(message)):
        load_profile(tmp_path, default=PROFILE)


def test_skolekit_has_no_defaults_of_its_own(tmp_path):
    # The toolkit reads a school only from its file or from the product's defaults.
    with pytest.raises(ProfileError, match="school.toml is missing"):
        load_profile(tmp_path)
    write(tmp_path, school=OTHER_SCHOOL)
    with pytest.raises(ProfileError, match="profile.toml is missing"):
        load_profile(tmp_path)
    write(tmp_path, school=OTHER_SCHOOL.replace('host = "eksempelskolen.m.skoleintra.dk"\n', ""),
          profile=OTHER_CHILDREN)
    with pytest.raises(ProfileError, match="host is required"):
        load_profile(tmp_path)
    assert load_profile(tmp_path, default=PROFILE).school.host == SCHOOL.host


# --- No household or school in the code ----------------------------------------------------

def test_no_school_host_or_portal_is_written_into_the_code():
    # A school's host and its portal come from school.toml; the code names neither.
    found = [str(path.relative_to(SRC)) for path in sorted(SRC.rglob("*.py"))
             if re.search(r"[a-z0-9-]+\.m\.skoleintra\.dk|/parent/", path.read_text(encoding="utf-8"))]
    assert found == []


# --- Where the profile is used -------------------------------------------------------------

def other_profile(tmp_path) -> SiteProfile:
    return sp.load_site_profile(write(tmp_path, school=OTHER_SCHOOL, profile=OTHER_CHILDREN))


def test_the_children_table_follows_the_profile(tmp_path):
    profile = other_profile(tmp_path)
    with SnapshotStore(tmp_path / "school.db") as store:
        typed_sources.sync_children(store.connection, profile.children)
        rows = store.connection.execute(
            "SELECT student_id, name, class_level, class_code FROM children ORDER BY student_id").fetchall()
    assert [tuple(row) for row in rows] == [(9101, "Alfa", 2, "02"), (9202, "Beta", 1, "01")]


def test_the_derivation_tells_the_model_the_profiles_class(tmp_path):
    candidate = {"sources": [{"payload_json": json.dumps({"title": "Tur"}), "child": "Alfa",
                              "version_id": 1}]}
    labels = other_profile(tmp_path).class_labels()
    assert derivation.candidate_document(candidate, class_labels=labels)["child_context"] == [
        {"child": "Alfa", "class": "2. årgang"}]
    # The labels are required: there is no household to fall back on.
    with pytest.raises(TypeError):
        derivation.candidate_document(candidate)


def test_review_checks_read_the_class_from_the_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    other_profile(tmp_path)
    assert review_checks.child_classes() == {"Alfa": 2, "Beta": 1}
    assert review_checks.class_label() == "{level}. årgang"
    item = {"child": "Alfa", "title": "Tur for 3. klasse", "effective_date": "2026-11-20"}
    verdict = review_checks.rule_verdict(item, [item], {"Alfa": 2}, "{level}. årgang")
    assert verdict == ("drop", None, "Marked for 3. årgang; Alfa is in 2. årgang.")

    seen = {}

    class Client:
        def generate(self, document, **kwargs):
            seen.update(document)
            return {"output": {"verdicts": []}}

    review_checks.model_verdicts(Client(), __import__("datetime").date(2026, 11, 13), {"Alfa": 2},
                                 [], [item], "{level}. årgang")
    assert seen["children"] == [{"name": "Alfa", "class_level": "2. årgang"}]


def test_day_summaries_write_the_class_with_the_label():
    assert day_summaries.class_level("2") == "2. klasse"
    assert day_summaries.class_level("2", "{level}. årgang") == "2. årgang"
    assert day_summaries.class_level("2.a") == "2.a"


def test_a_command_without_url_uses_the_profiles_pages(monkeypatch, tmp_path):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    other_profile(tmp_path)
    assert common.target_url(argparse.Namespace(url=None)).endswith("/foraelder/9101/Alfa/learningplan/student")
    assert common.target_url(argparse.Namespace(url=None), page="messages").endswith(
        "/foraelder/9101/Alfa/messages/conversations")
    given = "https://eksempelskolen.m.skoleintra.dk/foraelder/9101/Alfa/Index"
    assert common.target_url(argparse.Namespace(url=given)) == given
    with pytest.raises(ValueError):
        common.target_url(argparse.Namespace(url="https://example.com/"))
    # Another school's page is off this profile's host, so the read-only client won't reach it.
    with pytest.raises(ValueError, match="school's host, eksempelskolen.m.skoleintra.dk"):
        common.target_url(argparse.Namespace(url="https://andenskole.m.skoleintra.dk/parent/1/X/Index"))


def test_message_get_refuses_a_child_who_is_not_in_the_profile(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    other_profile(tmp_path)
    monkeypatch.setattr(messages, "_require_message_access", lambda args: "events-only")
    args = argparse.Namespace(thread_ref=None, child="Gamma")
    assert messages.command_message_get(args) == 2
    assert "--child must be one of: Alfa, Beta" in capsys.readouterr().err


def test_harvest_reads_the_profiles_children_and_login_page(monkeypatch, tmp_path):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    profile = other_profile(tmp_path)
    seen = {}
    SessionStore().save({"cookies": []})
    monkeypatch.setattr(pipeline, "session_is_valid",
                        lambda state, url: seen.setdefault("login_url", url) is not None)

    def collect_all(client, *, children, policy):
        seen["children"] = children
        return []

    monkeypatch.setattr(pipeline, "collect_all", collect_all)
    args = argparse.Namespace(
        db=str(tmp_path / "school-restricted.db"), mode="daily", calendar_past_weeks=None,
        calendar_future_weeks=None, weekly_plan_weeks=None, message_pages=None, frontpage_pages=None,
        auto_login=False, auto_login_timeout=2, message_mode="off", message_processor="disabled",
        model="synthetic/model-1")
    asyncio.run(lifecycle.command_harvest(args))
    assert seen["login_url"] == profile.login_url
    assert seen["children"] == profile.children
    with SnapshotStore(tmp_path / "school-restricted.db") as store:
        names = [row[0] for row in store.connection.execute("SELECT name FROM children ORDER BY name")]
    assert names == ["Alfa", "Beta"]


def test_harvest_stops_on_a_broken_profile(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    write(tmp_path, school=OTHER_SCHOOL, profile="children = []\n")
    monkeypatch.setattr(pipeline, "collect_all", lambda *args, **kwargs: pytest.fail("collected"))
    args = argparse.Namespace(
        db=str(tmp_path / "school-restricted.db"), mode="daily", calendar_past_weeks=None,
        calendar_future_weeks=None, weekly_plan_weeks=None, message_pages=None, frontpage_pages=None,
        auto_login=False, auto_login_timeout=2, message_mode="off", message_processor="disabled",
        model="synthetic/model-1")
    assert asyncio.run(lifecycle.command_harvest(args)) == 2
    assert "Invalid profile: profile.toml: list at least one [[children]]" in capsys.readouterr().err
