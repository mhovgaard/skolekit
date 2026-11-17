"""The formal store: migrations, typed sources, bookings and invariants."""
import argparse
import json
import sqlite3
from datetime import date

import pytest

from skoleklar.cli import database as database_cli

from skolekit import items
from skoleklar import bookings, cli, foundation, invariants, migrations
from tests_python.synthetic_household import SCHOOL as HOUSEHOLD_SCHOOL
from skoleklar.meaning import DEFAULT_MEANING
from skolekit.profile import Child as ChildConfig
from skolekit.profile import SiteProfile
from skoleklar.migrations import SchemaError
from skolekit.records import SourceCheck, SourceRecord
from skoleklar.storage import SCHEMA, SnapshotStore

CHILDREN = (ChildConfig(student_id=1, child="Beta", calendar_class_name="002", school=HOUSEHOLD_SCHOOL),
            ChildConfig(student_id=2, child="Alfa", calendar_class_name="000", school=HOUSEHOLD_SCHOOL))
PROFILE = SiteProfile(HOUSEHOLD_SCHOOL, CHILDREN)
BASE = "https://x.skoleintra.dk/parent"


def form(student_id=2, child="Alfa", form_id=344, *, status="", deadline="6. nov. 2026",
         title="Skole-hjem-samtale", list_state="closed", edit=False, kind="Samtale"):
    path = f"/parent/{student_id}/{child}/forms/{list_state}/conversations/{form_id}"
    path += "/edit" if edit else ""
    listing = {"Name": title, "Type": kind, "Status": status, "Deadline": deadline, "Url": path}
    return SourceRecord(
        student_id=student_id, child=child, source_type="forms",
        source_key=path if edit else f"conversations:{form_id}",
        source_url=f"https://x.skoleintra.dk{path}", title=title,
        source_data={"type": kind}, state_data={"list_state": list_state, "status": status},
        raw_payload={"list_item": listing})


def event(student_id=2, child="Alfa", event_id=29060, *, feed="SchoolEvents",
          start="2026-11-09T14:00:00.0000000", end="2026-11-09T19:00:00.0000000",
          invitees=("00",), title="Skole-hjem-samtaler"):
    raw = {"originalId": event_id, "id": f"0_{event_id:x}{feed[:2]}", "title": title,
           "start": start, "end": end, "allDay": False, "invitees": list(invitees)}
    return SourceRecord(
        student_id=student_id, child=child, source_type="calendar",
        source_key=f"{feed}:{event_id}", source_url=f"{BASE}/{student_id}/{child}/calendar",
        title=title, source_data={"calendar_kind": feed, "event": raw},
        state_data={"calendar_kind": feed}, raw_payload=raw)


def notice(student_id=2, child="Alfa", pin=3091, body=None):
    body = body or ("Skole-hjem-samtaler\nDato: 9. nov. 2026\n"
                    "Tilmelding: 16. okt. 2026 til 6. nov. 2026\nSamtalen varer ca. 15 minutter")
    return SourceRecord(
        student_id=student_id, child=child, source_type="frontpage",
        source_key=f"skoleintra-pin:{pin}", source_url=f"{BASE}/{student_id}/{child}/Index",
        title=body.split("\n", 1)[0], body_text=body, raw_payload={"body": body})


def scan(store, records, *, today, sources=("forms", "calendar", "frontpage")):
    """One harvest of complete checks per child and source, then the refresh."""
    run_id = store.begin_run()
    checks = []
    for child in CHILDREN:
        for source in sources:
            checks.append(SourceCheck(
                student_id=child.student_id, child=child.child, source_type=source,
                records=[r for r in records
                         if r.student_id == child.student_id and r.source_type == source]))
    store.store_checks(run_id, checks)
    return run_id, foundation.refresh(store.connection, run_id=run_id, today=today,
                                      profile=PROFILE, meaning=DEFAULT_MEANING)


def booking(store, student_id=2, key="conversations:344"):
    return store.connection.execute(
        "SELECT * FROM bookings WHERE student_id = ? AND form_key = ?", (student_id, key)).fetchone()


def history(store, student_id=2, key="conversations:344"):
    return [tuple(row) for row in store.connection.execute(
        "SELECT from_state, to_state, to_starts_local FROM booking_changes "
        "WHERE student_id = ? AND form_key = ? ORDER BY id", (student_id, key))]


# Migrations


def test_a_new_database_runs_every_migration_once(tmp_path):
    with SnapshotStore(tmp_path / "new.db") as store:
        rows = store.connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version").fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            (1, "baseline"), (2, "typed_sources"), (3, "bookings"), (4, "views"),
            (5, "item_archive"), (6, "message_celebrant")]
        assert [row[2] for row in rows] == [m.checksum for m in migrations.migrations()]
    with SnapshotStore(tmp_path / "new.db") as store:
        assert store.connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 6


def test_an_older_database_is_marked_as_baseline_without_rerunning_it(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO harvest_runs(started_at, status, schema_version) "
                           "VALUES ('2026-09-13T05:45:00Z', 'complete', 1)")
    with SnapshotStore(path) as store:
        assert migrations.current_version(store.connection) == 6
        assert store.connection.execute("SELECT count(*) FROM harvest_runs").fetchone()[0] == 1


def test_the_old_startup_upgrade_still_brings_an_older_file_to_the_baseline(tmp_path):
    path = tmp_path / "older.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        # A file from before these columns existed: rebuild the tables without them.
        connection.executescript("""
            DROP TABLE derivations;
            CREATE TABLE derivations (id INTEGER PRIMARY KEY,
                harvest_run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
                cache_key TEXT NOT NULL UNIQUE, provider TEXT NOT NULL, model TEXT NOT NULL,
                prompt_version TEXT NOT NULL, taxonomy_version TEXT NOT NULL,
                started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
                response_id TEXT, usage_json TEXT NOT NULL DEFAULT '{}', error TEXT);
            CREATE INDEX idx_derivations_harvest_run ON derivations(harvest_run_id, status);
        """)
    with SnapshotStore(path) as store:
        columns = {row[1] for row in store.connection.execute("PRAGMA table_info(derivations)")}
        assert "purpose" in columns
        assert migrations.current_version(store.connection) == 6


def test_a_file_that_does_not_match_the_baseline_is_refused_untouched(tmp_path):
    path = tmp_path / "odd.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("CREATE TABLE something_else (id INTEGER PRIMARY KEY)")
    with pytest.raises(SchemaError, match="unexpected table something_else"):
        SnapshotStore(path)
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert "schema_migrations" not in tables and "bookings" not in tables


def test_a_database_newer_than_the_code_is_refused(tmp_path):
    path = tmp_path / "newer.db"
    with SnapshotStore(path) as store:
        store.connection.execute("INSERT INTO schema_migrations VALUES (99, 'future', 'x', 'now')")
        store.connection.commit()
    with pytest.raises(SchemaError, match="newer than this code"):
        SnapshotStore(path)


def test_a_version_5_database_gains_the_celebrant_column_and_keeps_its_facts(tmp_path, monkeypatch):
    real = migrations.migrations()
    path = tmp_path / "v5.db"
    monkeypatch.setattr(migrations, "migrations", lambda: real[:5])
    with SnapshotStore(path) as store:
        assert "celebrant" not in {row[1] for row in store.connection.execute(
            "PRAGMA table_info(message_event_facts)")}
        store.connection.execute(
            "INSERT INTO message_event_facts(stable_key, thread_ref, message_ref, child, event_kind, "
            "effective_date, all_day, cancellation_state, activity_kind, participant_display_json, "
            "template_key, confidence, needs_review, workflow_state, extractor_version, policy_version, "
            "first_observed_at, last_confirmed_at) VALUES ('message-event:' || hex(zeroblob(32)), "
            "'thr_aaaaaaaaaaaaaaaa', 'msg_aaaaaaaaaaaaaaaa', 'Alfa', 'event', '2026-11-14', 1, 'active', "
            "'birthday_party', '[]', 'private_event', 0.9, 0, 'active', 'x', 'y', 'now', 'now')")
        store.connection.commit()
    monkeypatch.setattr(migrations, "migrations", lambda: real)
    with SnapshotStore(path) as store:
        assert migrations.current_version(store.connection) == 6
        [row] = store.connection.execute("SELECT activity_kind, celebrant FROM message_event_facts").fetchall()
        assert tuple(row) == ("birthday_party", None)


def test_an_edited_migration_is_refused(tmp_path):
    path = tmp_path / "edited.db"
    with SnapshotStore(path) as store:
        store.connection.execute("UPDATE schema_migrations SET checksum = 'edited' WHERE version = 3")
        store.connection.commit()
    with pytest.raises(SchemaError, match="0003_bookings differs"):
        SnapshotStore(path)


def test_a_failing_migration_leaves_nothing_behind(tmp_path, monkeypatch):
    real = migrations.migrations()
    broken = migrations.Migration(len(real) + 1, "broken", "CREATE TABLE half (id INTEGER);\nNOT SQL;", "c")
    monkeypatch.setattr(migrations, "migrations", lambda: (*real, broken))
    path = tmp_path / "broken.db"
    with pytest.raises(sqlite3.OperationalError):
        SnapshotStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == len(real)
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'half'").fetchone()[0] == 0


# Parsers


def test_danish_dates_and_booked_times_are_read_by_the_schools_wording():
    wording = HOUSEHOLD_SCHOOL.wording
    assert wording.read_date("Deadline 2. nov. 2026") == "2026-11-02"
    assert wording.read_date("12. maj 2026") == "2026-05-12"
    assert wording.read_date("31. feb. 2026") is None
    assert wording.read_dates("Dato: 5. nov. 2026, 9. nov. 2026") == ["2026-11-05", "2026-11-09"]
    assert wording.booked_time("Min tid: 9. nov. 2026 15:00") == "2026-11-09T15:00"
    assert wording.booked_time("Min tid: 9. nov. 2026 25:00") is None
    assert wording.booked_time("Ikke besvaret") is None
    assert wording.answered_on("Besvaret d. 3. nov. 2026") == "2026-11-03"
    assert wording.signup_window("Tilmelding: 1. nov. 2026 til 5. nov. 2026\nx") == ("2026-11-01", "2026-11-05")
    assert wording.expires_on("Udløbsdato: 6. nov. 2026") == "2026-11-06"
    assert wording.notice_minutes("Samtalen varer ca. 20 min.") == 20
    assert wording.notice_minutes("varer ca. 2 min") is None


def test_a_form_keeps_one_key_whatever_its_link_does():
    for url in ("https://x/parent/2/A/forms/open/conversations/344/edit",
                "https://x/parent/2/A/forms/closed/conversations/344"):
        assert items.form_key(url, url) == "conversations:344"
    assert items.form_key("conversations:344", "") == "conversations:344"
    assert items.form_key("something", "https://x/elsewhere") is None


def test_class_levels_come_from_invitee_codes_and_the_title():
    assert items.class_levels("Badminton 1 & 2 klasse", "", ["Line"]) == {1, 2}
    assert items.class_levels("Samtaler", "", ["Teacher", "02"]) == {2}
    assert len(items.class_levels("Morgensang", "", [f"{n:02d}" for n in range(10)])) == 10


def test_the_typed_tables_hold_one_row_per_form_event_and_notice(tmp_path):
    records = [form(status="Min tid: 9. nov. 2026 15:00"),
               event(feed="SchoolEvents"), event(feed="ActivitiesEvents"), notice()]
    with SnapshotStore(tmp_path / "s.db") as store:
        _, result = scan(store, records, today=date(2026, 11, 8))
        rows = result["typed_sources"]["rows"]
        assert rows == {"src_forms": 1, "src_calendar_events": 1, "src_calendar_event_levels": 1,
                        "src_notices": 1, "src_notice_days": 1}
        typed = store.connection.execute("SELECT * FROM src_forms").fetchone()
        assert (typed["booked_local"], typed["deadline"], typed["list_state"]) == (
            "2026-11-09T15:00", "2026-11-06", "closed")
        calendar = store.connection.execute("SELECT * FROM src_calendar_events").fetchone()
        assert calendar["event_id"] == 29060 and calendar["starts_local"] == "2026-11-09T14:00"
        assert calendar["school_version_id"] and calendar["activities_version_id"]
        typed_notice = store.connection.execute("SELECT * FROM src_notices").fetchone()
        assert (typed_notice["signup_until"], typed_notice["minutes"]) == ("2026-11-06", 15)


def test_a_row_the_schema_refuses_is_left_out_and_counted(tmp_path):
    backwards = event(start="2026-11-09T19:00:00", end="2026-11-09T14:00:00")
    with SnapshotStore(tmp_path / "s.db") as store:
        _, result = scan(store, [backwards], today=date(2026, 11, 8))
    assert result["typed_sources"]["rows"]["src_calendar_events"] == 0
    assert result["typed_sources"]["rejected"] == 1


# Bookings


def test_a_booking_links_its_round_and_becomes_held_the_day_after(tmp_path):
    records = [form(status="Min tid: 9. nov. 2026 15:00"), event(), notice()]
    with SnapshotStore(tmp_path / "s.db") as store:
        scan(store, records, today=date(2026, 11, 8))
        row = booking(store)
        assert (row["kind"], row["state"], row["starts_local"]) == ("conference", "booked", "2026-11-09T15:00")
        assert (row["minutes"], row["minutes_source"]) == (15, "notice")
        assert (row["notice_key"], row["window_event_id"]) == ("skoleintra-pin:3091", 29060)
        scan(store, records, today=date(2026, 11, 10))
        assert booking(store)["state"] == "held"
        assert history(store) == [(None, "booked", "2026-11-09T15:00"),
                                  ("booked", "held", "2026-11-09T15:00")]


def test_without_a_notice_the_length_is_thirty_minutes_and_a_nurse_visit_is_health(tmp_path):
    records = [form(student_id=1, child="Beta", form_id=308, title="Sundhedsplejerske",
                    status="Min tid: 26. mar. 2025 13:15", deadline="26. mar. 2025")]
    with SnapshotStore(tmp_path / "s.db") as store:
        scan(store, records, today=date(2026, 11, 8))
        row = booking(store, 1, "conversations:308")
    assert (row["kind"], row["state"], row["minutes"], row["minutes_source"]) == (
        "health", "held", 30, "default")


def test_an_open_form_becomes_booked_or_missed(tmp_path):
    with SnapshotStore(tmp_path / "s.db") as store:
        scan(store, [form(list_state="open", edit=True)], today=date(2026, 11, 1))
        assert booking(store)["state"] == "open"
        scan(store, [form(list_state="open", edit=True, status="Min tid: 9. nov. 2026 15:00")],
             today=date(2026, 11, 2))
        assert booking(store)["state"] == "booked"
    with SnapshotStore(tmp_path / "t.db") as store:
        scan(store, [form(list_state="open", edit=True)], today=date(2026, 11, 1))
        scan(store, [form()], today=date(2026, 11, 7))
        assert booking(store)["state"] == "missed"


def test_the_round_closing_keeps_the_booking_under_its_key(tmp_path):
    with SnapshotStore(tmp_path / "s.db") as store:
        first, _ = scan(store, [form(list_state="open", edit=True, status="Min tid: 9. nov. 2026 15:00")],
                        today=date(2026, 10, 23))
        scan(store, [form(status="Min tid: 9. nov. 2026 15:00")], today=date(2026, 11, 7))
        row = booking(store)
        assert row["first_seen_run_id"] == first
        assert store.connection.execute("SELECT count(*) FROM bookings").fetchone()[0] == 1
        assert history(store) == [(None, "booked", "2026-11-09T15:00")]


def test_a_slot_booked_and_held_between_two_scans_goes_through_booked(tmp_path):
    with SnapshotStore(tmp_path / "s.db") as store:
        scan(store, [form(list_state="open", edit=True)], today=date(2026, 11, 1))
        scan(store, [form(status="Min tid: 9. nov. 2026 15:00")], today=date(2026, 11, 10))
        assert [step[:2] for step in history(store)] == [(None, "open"), ("open", "booked"),
                                                         ("booked", "held")]


def test_a_withdrawn_time_or_a_vanished_form_cancels_the_booking(tmp_path):
    with SnapshotStore(tmp_path / "s.db") as store:
        scan(store, [form(list_state="open", edit=True, status="Min tid: 9. nov. 2026 15:00")],
             today=date(2026, 11, 1))
        scan(store, [form(list_state="open", edit=True)], today=date(2026, 11, 2))
        row = booking(store)
        assert (row["state"], row["starts_local"]) == ("cancelled", "2026-11-09T15:00")
    with SnapshotStore(tmp_path / "t.db") as store:
        scan(store, [form(status="Min tid: 9. nov. 2026 15:00")], today=date(2026, 11, 7))
        for _ in range(2):  # missing, then removed
            scan(store, [], today=date(2026, 11, 8))
        assert booking(store)["state"] == "cancelled"


def test_the_owner_s_change_stands_until_the_form_changes(tmp_path):
    records = [form(status="Min tid: 9. nov. 2026 15:00")]
    with SnapshotStore(tmp_path / "s.db") as store:
        scan(store, records, today=date(2026, 11, 7))
        store.connection.execute(
            "UPDATE bookings SET state = 'cancelled', updated_by = 'owner', updated_at = 'now' "
            "WHERE form_key = 'conversations:344'")
        store.connection.commit()
        _, result = scan(store, records, today=date(2026, 11, 8))
        assert result["bookings"]["skipped_owner"] == 1
        assert booking(store)["state"] == "cancelled"


def test_the_database_refuses_disallowed_moves_deletes_and_history_edits(tmp_path):
    with SnapshotStore(tmp_path / "s.db") as store:
        scan(store, [form(status="Min tid: 9. nov. 2026 15:00")], today=date(2026, 11, 10))
        connection = store.connection
        for sql in ("UPDATE bookings SET state = 'open'",
                    "DELETE FROM bookings",
                    "UPDATE booking_changes SET to_state = 'open'",
                    "DELETE FROM booking_changes"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)
            connection.rollback()


def test_moves_go_only_where_the_transitions_allow():
    allowed = {("open", "booked"), ("open", "missed"), ("open", "cancelled"), ("missed", "booked"),
               ("booked", "cancelled"), ("booked", "held"), ("cancelled", "booked")}
    assert bookings.moves("open", "held", allowed) == ["booked", "held"]
    assert bookings.moves("booked", "missed", allowed) == ["cancelled"]
    assert bookings.moves("held", "booked", allowed) == []
    assert bookings.moves("cancelled", "open", allowed) == []


# Invariants and queries


def test_the_invariants_pass_on_a_scanned_store_and_catch_an_overlap(tmp_path):
    records = [form(status="Min tid: 9. nov. 2026 15:00"),
               form(form_id=345, status="Min tid: 9. nov. 2026 15:10"), event(), notice()]
    with SnapshotStore(tmp_path / "s.db") as store:
        scan(store, records[:1] + records[2:], today=date(2026, 11, 8))
        assert invariants.check(store.connection) == []
        scan(store, records, today=date(2026, 11, 8))
        failures = dict(invariants.check(store.connection))
    assert list(failures) == ["no child has overlapping booked slots"]


def test_the_invariants_report_an_unmigrated_file(tmp_path):
    connection = sqlite3.connect(tmp_path / "empty.db")
    assert [name for name, _ in invariants.check(connection)] == ["schema is at the code's version"]


def test_saved_queries_run_on_a_read_only_copy(tmp_path, capsys):
    path = tmp_path / "s.db"
    with SnapshotStore(path) as store:
        scan(store, [form(status="Min tid: 9. nov. 2026 15:00"), event(), notice()],
             today=date(2026, 11, 8))
    args = argparse.Namespace(name="bookings", term=None, db=str(path), json=True)
    assert database_cli.command_db_query(args) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(row["child"], row["state"], row["starts"]) for row in rows] == [
        ("Alfa", "booked", "2026-11-09 15:00")]
    assert database_cli.command_db_check(argparse.Namespace(db=str(path))) == 0
    assert "FAIL" not in capsys.readouterr().out
    with pytest.raises(ValueError, match="needs a search term"):
        database_cli.command_db_query(argparse.Namespace(name="trace", term=None, db=str(path), json=False))


def test_the_collector_keys_an_open_form_like_its_closed_self():
    from skolekit.sources.forms import FORM_ID_PATTERN
    for path in ("/parent/2/Alfa/forms/open/conversations/344/edit",
                 "/parent/2/Alfa/forms/closed/conversations/344"):
        match = FORM_ID_PATTERN.search(path)
        assert (match.group("kind"), match.group("id")) == ("conversations", "344")


def test_the_archive_results_live_in_item_archive_and_the_views_read_them(tmp_path):
    # Migration 0005: the archive stage's six columns left item_workflow.
    with SnapshotStore(tmp_path / "s.db") as store:
        workflow = {row[1] for row in store.connection.execute("PRAGMA table_info(item_workflow)")}
        archive = [row[1] for row in store.connection.execute("PRAGMA table_info(item_archive)")]
        live = [row[1] for row in store.connection.execute("PRAGMA table_info(v_live_facts)")]
    assert not {column for column in workflow if column.startswith("archive")} and "archived_at" not in workflow
    assert archive == ["stable_key", "archived_at", "archive_reason", "archive_run_id", "reviewed_at",
                       "review_as_of", "review_reason"]
    assert live[-8:] == ["archived_at", "archive_reason", "archive_run_id", "archive_reviewed_at",
                         "archive_review_as_of", "archive_review_reason", "workflow_first_seen_at",
                         "workflow_last_seen_at"]
