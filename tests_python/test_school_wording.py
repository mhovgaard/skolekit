"""Phase 4: the school's wording in school.toml, the household's meaning in skoleklar.toml.

A school.toml spelling out every locale default reads as the same school as
its host alone, so the typed tables and the bookings are the same with either
(fixtures/school/). Another school's words and another household's meaning
are read from the files. No word the parsers look for is left in the code.
Synthetic children only.
"""
import argparse
import json
import re
import shutil
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from skolekit import cli as kit_cli
from skolekit.parsers.weekly_plan import segment_content
from skolekit.profile import Child, ProfileError, SiteProfile, load_profile
from skoleklar.store.check import check_database
from skolekit.wording import DANISH
from skoleklar import cli, foundation
from skoleklar.triage import review_checks
from skoleklar.household import load_site_profile
from tests_python.synthetic_household import SCHOOL as HOUSEHOLD_SCHOOL
from tests_python.synthetic_household import write_household
from skoleklar.meaning import DEFAULT_MEANING, load_meaning
from skoleklar.storage import SnapshotStore
from skolekit.records import SourceCheck
from tests_python.test_foundation import event, form, notice

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "school"
TODAY = date(2026, 11, 8)


def children(school):
    return (Child(student_id=1, child="Alfa", calendar_class_name="002", school=school),
            Child(student_id=2, child="Beta", calendar_class_name="000", school=school))


def records():
    beta = {"student_id": 2, "child": "Beta"}
    alfa = {"student_id": 1, "child": "Alfa"}
    return [
        form(**beta, status="Min tid: 9. nov. 2026 15:00"),
        form(**beta, form_id=345, status="Besvaret 2. nov. 2026", title="Udflugt", kind="Spørgeskema"),
        form(**alfa, form_id=308, title="Sundhedsplejerske", status="Min tid: 26. mar. 2025 13:15",
             deadline="26. mar. 2025"),
        form(**alfa, form_id=309, status="Min tid: 31. nov. 2026 10:00", deadline="1. dec. 2026"),
        event(**beta), event(**beta, feed="ActivitiesEvents"),
        event(**beta, event_id=29070, title="Morgensang for alle klasser",
              invitees=[f"{level:02d}" for level in range(10)]),
        notice(**beta),
        notice(**alfa, pin=3092, body="Juleklip\nUdløbsdato: 18. dec. 2026\nVarer ca. 400 min"),
    ]


def scanned(path: Path, profile: SiteProfile, meaning=DEFAULT_MEANING, rows=None):
    """A store holding `rows` (today's records by default), refreshed with this profile and meaning."""
    rows = records() if rows is None else rows
    store = SnapshotStore(path)
    run_id = store.begin_run()
    store.store_checks(run_id, [
        SourceCheck(student_id=child.student_id, child=child.child, source_type=source,
                    records=[r for r in rows if r.student_id == child.student_id and r.source_type == source])
        for child in profile.children for source in ("forms", "calendar", "frontpage")])
    result = foundation.refresh(store.connection, run_id=run_id, today=TODAY, profile=profile,
                                meaning=meaning)
    return store, result


def dump(store) -> dict[str, list[tuple]]:
    """The typed tables and the bookings, without the times a refresh was written."""
    tables = {
        "src_forms": "*", "src_calendar_events": "*", "src_calendar_event_levels": "*",
        "src_notices": "*", "src_notice_days": "*",
        "src_refreshes": "run_id, parser_version, row_counts, rejected_count",
        "bookings": "student_id, form_key, state, kind, title, starts_local, minutes, minutes_source, "
                    "deadline, notice_key, window_event_id, source_version_id",
        "booking_changes": "student_id, form_key, from_state, to_state, to_starts_local, to_minutes",
    }
    return {table: [tuple(row) for row in store.connection.execute(
        f"SELECT {columns} FROM {table} ORDER BY 1, 2, 3")] for table, columns in tables.items()}


def home_with(tmp_path: Path, **files: str) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    for name, text in files.items():
        (home / name).write_text(text, encoding="utf-8")
    return home


# --- Today's values are the defaults ----------------------------------------------------------

def test_a_spelled_out_school_is_the_same_school_as_its_host_alone(tmp_path):
    home = tmp_path / "home"
    shutil.copytree(FIXTURES, home)
    (home / "profile.toml").write_text('[[children]]\nstudent_id = 9101\nname = "Alfa"\ncalendar_class_name = "001"\n',
                                       encoding="utf-8")
    profile = load_site_profile(home)
    assert profile.school == HOUSEHOLD_SCHOOL and profile.school.wording == DANISH
    assert profile.school.digest() == HOUSEHOLD_SCHOOL.digest()
    assert load_meaning(home) == DEFAULT_MEANING


def test_the_typed_tables_and_bookings_are_the_same_with_and_without_the_files(tmp_path):
    home = tmp_path / "home"
    shutil.copytree(FIXTURES, home)
    default_profile = SiteProfile(HOUSEHOLD_SCHOOL, children(HOUSEHOLD_SCHOOL))
    spelled_out = load_profile(home, default=default_profile)
    assert spelled_out.sources == ("school.toml",)
    without, _ = scanned(tmp_path / "without.db", default_profile)
    with_files, _ = scanned(tmp_path / "with.db", spelled_out, load_meaning(home))
    first, second = dump(without), dump(with_files)
    assert first == second
    # The scenario reads every rule: a booked and an answered form, a nurse visit,
    # an impossible date, a sign-up round, an expiry, a stated and an unlikely length.
    forms = {row[1]: row for row in first["src_forms"]}
    assert len(forms) == 4 and len(first["src_notices"]) == 2 and len(first["bookings"]) == 3
    without.close()
    with_files.close()


def test_each_refresh_records_which_rules_read_it(tmp_path):
    store, result = scanned(tmp_path / "s.db", SiteProfile(HOUSEHOLD_SCHOOL, children(HOUSEHOLD_SCHOOL)))
    version = store.connection.execute("SELECT parser_version FROM src_refreshes").fetchone()[0]
    assert version == result["typed_sources"]["parser_version"] == f"typed-v1 school:{HOUSEHOLD_SCHOOL.digest()}"
    store.close()
    changed = replace(HOUSEHOLD_SCHOOL, wording=replace(DANISH, broad=("hele skolen",)))
    assert changed.digest() != HOUSEHOLD_SCHOOL.digest()
    assert replace(HOUSEHOLD_SCHOOL, class_label="{level}. årgang").digest() != HOUSEHOLD_SCHOOL.digest()


# --- Another school's words -------------------------------------------------------------------

OTHER_WORDING = """\
[site]
host = "eksempelskolen.m.skoleintra.dk"

[classes]
broad = ["hele skolen"]

[forms]
booked = '(?i)^\\s*Din tid:\\s*{date}\\s+kl\\.\\s*{time}'

[notices]
minutes = '(?i)varighed:\\s*{minutes}\\s*minutter'

[weekly_plan.sections]
homework = ['Hjemmearbejde']
"""


def test_another_schools_words_are_read_from_its_file(tmp_path):
    profile = load_site_profile(home_with(tmp_path, **{
        "school.toml": OTHER_WORDING,
        "profile.toml": '[[children]]\nstudent_id = 9101\nname = "Alfa"\ncalendar_class_name = "001"\n'}))
    school = profile.school
    assert school.host == "eksempelskolen.m.skoleintra.dk"
    wording = school.wording
    assert wording.booked_time("Din tid: 9. nov. 2026 kl. 15.00") == "2026-11-09T15:00"
    assert wording.booked_time("Min tid: 9. nov. 2026 15:00") is None
    assert wording.notice_minutes("Varighed: 20 minutter") == 20
    # What the file leaves out stays the locale's.
    assert wording.answered == DANISH.answered and wording.signup == DANISH.signup
    assert wording.section_kind("Hjemmearbejde") == "homework"
    assert wording.section_kind("Lektier") == "unsectioned"
    assert wording.section_kind("Medbring") == "bring"
    assert [segment["section_hint"] for segment in segment_content("Hjemmearbejde: læs s. 4", wording)] == [
        "homework"]
    assert wording.names_several_classes("Tur for hele skolen")
    assert not wording.names_several_classes("Tur for alle klasser")

    rows = [form(student_id=2, child="Beta", status="Din tid: 9. nov. 2026 kl. 15.00"),
            notice(student_id=2, child="Beta", body="Samtaler\nTilmelding: 1. nov. 2026 til 6. nov. 2026\n"
                                                    "Varighed: 20 minutter")]
    store, _ = scanned(tmp_path / "other.db", SiteProfile(school, children(school)), rows=rows)
    booked, minutes = store.connection.execute(
        "SELECT f.booked_local, n.minutes FROM src_forms f, src_notices n").fetchone()
    assert (booked, minutes) == ("2026-11-09T15:00", 20)
    row = store.connection.execute("SELECT state, minutes, minutes_source FROM bookings").fetchone()
    assert tuple(row) == ("booked", 20, "notice")
    store.close()


def test_the_review_rule_reads_the_schools_words_for_several_classes():
    item = {"child": "Alfa", "title": "Tur for 3. klasse og hele skolen", "effective_date": "2026-11-20"}
    assert review_checks.rule_verdict(item, [item], {"Alfa": 2}) is None
    narrow = replace(DANISH, broad=("indskolingen",))
    verdict = review_checks.rule_verdict(item, [item], {"Alfa": 2}, wording=narrow)
    assert verdict == ("drop", None, "Marked for 3. klasse; Alfa is in 2. klasse.")


# --- Another household's meaning -------------------------------------------------------------

def test_another_households_meaning_is_read_from_its_file(tmp_path):
    home = home_with(tmp_path, **{"skoleklar.toml": """\
[meaning.bookings]
form_types = ["Samtale", "Spørgeskema"]
kinds = { health = "sundhedspl", sfo = "udflugt" }
default_minutes = 20
"""})
    meaning = load_meaning(home)
    assert meaning.default_minutes == 20 and meaning.round == "signup_until"
    store, _ = scanned(tmp_path / "s.db", SiteProfile(HOUSEHOLD_SCHOOL, children(HOUSEHOLD_SCHOOL)), meaning)
    rows = {row["form_key"]: row for row in store.connection.execute("SELECT * FROM bookings")}
    assert rows["conversations:345"]["kind"] == "sfo"
    assert rows["conversations:308"]["kind"] == "health"
    assert (rows["conversations:308"]["minutes"], rows["conversations:308"]["minutes_source"]) == (20, "default")
    store.close()


@pytest.mark.parametrize("text, message", [
    ("[meaning.bookings]\nround = \"first_notice\"\n", "only signup_until"),
    ("[meaning.bookings]\ndefault_minutes = 500\n", "5 to 240"),
    ("[meaning.bookings]\nkinds = { trip = \"tur\" }\n", "'trip' is not one of"),
    ("[meaning.bookings]\nform_types = []\n", "form_types"),
    ("[meaning.bookings]\nlength = 3\n", "unknown key 'length'"),
    ("[meaning.calendar]\nschool_wide = \"three_codes\"\n", "only every_class_code"),
    ("[meaning.lunch]\nx = 1\n", "unknown table [meaning.lunch]"),
    ("[site]\nhost = \"x\"\n", "unknown table [site]"),
    ("[meaning\n", "skoleklar.toml"),
])
def test_a_meaning_that_cant_be_used_as_written_is_refused(tmp_path, text, message):
    with pytest.raises(ProfileError, match=re.escape(message)):
        load_meaning(home_with(tmp_path, **{"skoleklar.toml": text}))


# --- No school wording in the code -----------------------------------------------------------

# The words the parsers and the bookings look for. Only locales/ and the
# product's meaning.toml may hold them.
SCHOOL_WORDS = ["Min tid", "Besvaret", "Tilmelding", "Udløbsdato", "Dato:", r"varer\b", "alle klasser",
                "hele skolen", "alle elever", "indskolingen", "mellemtrinnet", "sundhedspl", "Samtale"]
# A weekly plan's headings, the month names and the inbox's relative days. The
# product's board writes some of the same words as its own labels ('Medbring:',
# 'Lektie:', 'i dag'; manifests/dashboard-v2), so these are checked in the toolkit only.
TOOLKIT_WORDS = [r"Lektie", r"Materiale", r"Medbring", r"Aktivitet", r"Dagens", r"\bProgram\b",
                 r"[\"']maj[\"']", r"[\"']okt[\"']", r"i går", r"i dag", r"aflyst"]


def _offenders(root: Path, words: list[str]) -> list[str]:
    pattern = re.compile("|".join(words))
    return [f"{path.relative_to(ROOT / 'src')}: {match.group(0)}"
            for path in sorted(root.rglob("*.py"))
            for match in [pattern.search(path.read_text(encoding="utf-8"))] if match]


def test_no_school_wording_is_left_in_the_code():
    assert _offenders(ROOT / "src", SCHOOL_WORDS) == []
    assert _offenders(ROOT / "src" / "skolekit", TOOLKIT_WORDS) == []
    # The guard looks where the words now live.
    locale = (ROOT / "src" / "skolekit" / "locales" / "da.toml").read_text(encoding="utf-8")
    assert all(re.search(word, locale) for word in SCHOOL_WORDS if word not in ("sundhedspl", "Samtale"))
    assert all(re.search(word, locale) for word in TOOLKIT_WORDS[:5] + TOOLKIT_WORDS[-2:])
    meaning = (ROOT / "src" / "skoleklar" / "meaning.toml").read_text(encoding="utf-8")
    assert "sundhedspl" in meaning and "Samtale" in meaning


# --- skolekit school check -------------------------------------------------------------------

def test_school_check_counts_what_each_rule_read_and_never_prints_text(tmp_path):
    store, _ = scanned(tmp_path / "s.db", SiteProfile(HOUSEHOLD_SCHOOL, children(HOUSEHOLD_SCHOOL)))
    store.close()
    report = check_database(tmp_path / "s.db", HOUSEHOLD_SCHOOL)
    assert report["school"] == HOUSEHOLD_SCHOOL.digest()
    rules = report["rules"]
    # Four forms: two booked times, one of them on a day that doesn't exist, and one answer.
    assert rules["forms.booked"] == {"matched": 2, "unmatched": 1, "refused": 1}
    assert rules["forms.answered"] == {"matched": 1, "unmatched": 3, "refused": 0}
    # Two notices: one round with its sign-up, days and length; one expiry with an unlikely length.
    assert rules["notices.signup"] == {"matched": 1, "unmatched": 1, "refused": 0}
    assert rules["notices.expires"] == {"matched": 1, "unmatched": 1, "refused": 0}
    assert rules["notices.days"] == {"matched": 1, "unmatched": 1, "refused": 0}
    assert rules["notices.minutes"] == {"matched": 1, "unmatched": 0, "refused": 1}
    assert rules["classes.broad"]["matched"] == 1
    assert set(rules["weekly_plan.sections"]) == {"matched", "unmatched", "refused", "kinds"}
    text = json.dumps(report, ensure_ascii=False)
    assert not re.search(r"Sundhed|Morgensang|Juleklip|Udflugt|Alfa|Beta", text)


def test_skolekit_needs_the_schools_files(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    assert kit_cli.main(["school", "show"]) == 2
    assert "school.toml is missing" in capsys.readouterr().err
    shutil.copy(FIXTURES / "school.toml", tmp_path / "school.toml")
    (tmp_path / "profile.toml").write_text(
        '[[children]]\nstudent_id = 1\nname = "Alfa"\ncalendar_class_name = "002"\n', encoding="utf-8")
    assert kit_cli.main(["school", "show"]) == 0
    assert json.loads(capsys.readouterr().out)["rules"] == HOUSEHOLD_SCHOOL.digest()


def test_skoleklar_checks_the_stored_pages_with_the_households_files(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(write_household(tmp_path)))
    store, _ = scanned(tmp_path / "school-restricted.db", SiteProfile(HOUSEHOLD_SCHOOL, children(HOUSEHOLD_SCHOOL)))
    store.close()
    args = cli.build_parser().parse_args(["school", "check"])
    assert args.handler(args) == 0
    assert json.loads(capsys.readouterr().out)["rules"]["forms.booked"]["matched"] == 2
    # Read-only: the check opened the file without writing to it.
    with sqlite3.connect(tmp_path / "school-restricted.db") as connection:
        assert connection.execute("SELECT count(*) FROM src_refreshes").fetchone()[0] == 1
    assert isinstance(args, argparse.Namespace)
