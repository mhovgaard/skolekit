"""Phase 6: skolekit's live view of one child, on the synthetic site.

Each read defaults to the current view (the site's clock is Thursday of week
46), returns typed items, sends only GETs, and stores nothing. The CLI prints
the same results with exit codes a scheduler can act on.
"""
import json
from pathlib import Path

import httpx
import pytest

from skolekit import cli as kit_cli
from skolekit import live
from skolekit.auth.session import SessionStore
from skolekit.sources import calendar as calendar_source
from skolekit.sources.base import CollectionError
from skolekit.transport import ReadOnlyClient
from skoleklar import cli as product_cli
from tests_python.skoleintra_site import ALFA, BETA, SCHOOL, SyntheticSite, frozen_datetime

WEEK_47 = live.Week(2026, 47)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(live, "datetime", frozen_datetime())
    monkeypatch.setattr(calendar_source, "datetime", frozen_datetime())


@pytest.fixture
def site() -> SyntheticSite:
    return SyntheticSite()


def read(site, name, child=ALFA, **options):
    with site.client() as client:
        result = live.READS[name](client, child, **options)
    assert site.forbidden == []
    assert {request.method for request in site.requests} == {"GET"}
    return result


# --- Weeks -------------------------------------------------------------------------------------

def test_a_week_is_named_by_number_iso_week_or_a_date():
    today = live.now(ALFA).date()
    assert live.Week.of(today) == live.Week(2026, 46)
    assert live.Week.parse("47", today) == live.Week.parse("2026-W47", today) == WEEK_47
    assert live.Week.parse("2026-11-19", today) == WEEK_47
    assert WEEK_47.label == "2026-W47" and WEEK_47.monday.isoformat() == "2026-11-16"
    for wrong in ("54", "next", "2026-W60"):
        with pytest.raises(ValueError):
            live.Week.parse(wrong, today)


# --- The timetable and the calendar ---------------------------------------------------------------

def test_the_schedule_is_this_weeks_lessons_from_the_lessons_feed_only(site):
    result = read(site, "schedule")
    assert result["week"] == "2026-W46" and result["source"] == "site" and result["child"] == "Alfa"
    assert [route for _, route in site.routes()] == ["calendareventsource/LessonsEvents"]
    params = dict(site.requests[0].url.params)
    assert params["className"] == ALFA.calendar_class_name
    # One chunk: Monday 9 November to Monday 16 November.
    assert int(params["end"]) - int(params["start"]) == 7 * 86400
    lessons = result["lessons"]
    assert lessons and [lesson["starts_local"] for lesson in lessons] == sorted(
        lesson["starts_local"] for lesson in lessons)
    assert set(lessons[0]) == {"starts_local", "ends_local", "subject", "class", "location", "teacher",
                               "substitute", "absent", "note", "cancelled", "changed"}
    assert result["changed"] == sum(lesson["changed"] for lesson in lessons)


def test_a_lesson_says_when_it_is_cancelled_or_has_a_substitute():
    from skolekit.items import read_lesson

    wording = SCHOOL.wording
    base = {"start": "2026-11-12T08:00:00", "end": "2026-11-12T08:45:00", "lessonName": "Dansk",
            "staffName": "KL"}
    assert read_lesson(base, wording=wording)["changed"] is False
    cancelled = read_lesson({**base, "title": "Dansk - aflyst"}, wording=wording)
    assert cancelled["cancelled"] and cancelled["changed"]
    substitute = read_lesson({**base, "substituteStaff": "MB"}, wording=wording)
    assert substitute["substitute"] == "MB" and substitute["changed"]
    # The site repeats the ordinary teacher as a substitute: that is no change.
    same = read_lesson({**base, "substituteStaff": "kl"}, wording=wording)
    assert same["substitute"] is None and same["changed"] is False


def test_the_calendar_shows_the_childs_class_and_the_whole_school_once_per_event(site):
    result = read(site, "calendar", week=WEEK_47)
    assert {route for _, route in site.routes()} == {"calendareventsource/ActivitiesEvents",
                                                     "calendareventsource/SchoolEvents"}
    events = {event["title"]: event for event in result["events"]}
    assert set(events) == {"Forældremøde 1. klasse", "Skolefoto", "Motionsdag"}
    assert events["Forældremøde 1. klasse"]["for"] == "class"
    assert events["Motionsdag"]["for"] == "school"
    assert sorted(events["Motionsdag"]["feeds"]) == ["ActivitiesEvents", "SchoolEvents"]
    assert events["Skolefoto"]["for"] == "unspecified"
    assert result["other_classes_hidden"] == 0


def test_an_event_only_for_other_classes_is_hidden_unless_asked(site):
    # Beta is in 3. klasse; Alfa's parents' meeting is for 1. klasse only.
    meeting = {"originalId": 880101, "title": "Forældremøde 1. klasse", "start": "2026-11-17T17:00:00",
               "end": "2026-11-17T18:00:00", "allDay": False, "invitees": ["01"]}
    site.override(BETA, "calendareventsource/ActivitiesEvents", lambda request: httpx.Response(200, json=[meeting]))
    hidden = read(site, "calendar", BETA, week=WEEK_47)
    assert [event["title"] for event in hidden["events"]] == ["Skolefoto"]
    assert hidden["other_classes_hidden"] == 1
    shown = read(SyntheticSite(), "calendar", BETA, week=WEEK_47, all_classes=True)
    assert "Skolefoto" in [event["title"] for event in shown["events"]]


# --- Weekly plans, forms and notices ----------------------------------------------------------------

def test_the_weekly_plan_is_the_current_week_with_its_sections(site):
    result = read(site, "weekly_plan")
    assert result["week"] == "2026-W46"
    assert result["available"] == ["2026-W09", "2026-W46", "2026-W47"]
    monday = result["days"][0]
    assert monday["date"] == "2026-11-09"
    kinds = [section["kind"] for lesson in monday["lessons"] for section in lesson["sections"]]
    assert "homework" in kinds
    # The list and one week's page: nothing else is read.
    assert [route for _, route in site.routes()] == [
        "item/weeklyplansandhomework/list", "item/weeklyplansandhomework/item/class/46-2026"]


def test_another_week_is_read_when_asked_and_a_missing_one_is_refused(site):
    assert read(site, "weekly_plan", week=WEEK_47)["week"] == "2026-W47"
    with pytest.raises(CollectionError, match="no weekly plan for 2026-W48"):
        read(SyntheticSite(), "weekly_plan", week=live.Week(2026, 48))


def test_forms_carry_their_status_and_dates(site):
    result = read(site, "forms")
    assert {form["list_state"] for form in result["forms"]} == {"open", "closed"}
    assert all(set(form) >= {"form_key", "title", "status_text", "booked_local", "deadline", "url"}
               for form in result["forms"])
    only_open = read(SyntheticSite(), "forms", state="open")
    assert {form["list_state"] for form in only_open["forms"]} == {"open"}


def test_notices_past_their_expiry_are_left_out_unless_asked(site, monkeypatch):
    shown = read(site, "notices")
    assert shown["notices"] and shown["expired_hidden"] == 0
    # Later in the season every notice with an expiry has passed it.
    monkeypatch.setattr(live, "datetime", frozen_datetime(live.datetime(2027, 6, 1, 12, tzinfo=live.ZoneInfo(
        "Europe/Copenhagen"))))
    later = read(SyntheticSite(), "notices")
    every = read(SyntheticSite(), "notices", include_expired=True)
    expired = [notice for notice in every["notices"] if notice["expired"]]
    assert later["expired_hidden"] == len(expired)
    assert len(later["notices"]) + len(expired) == len(every["notices"])


# --- Messages ---------------------------------------------------------------------------------------

def test_the_inbox_lists_subjects_and_ids_but_no_text(site):
    result = read(site, "messages")
    assert [route for _, route in site.routes()] == ["messages/conversations"]
    threads = result["threads"]
    assert [thread["thread_id"] for thread in threads] == [301, 302, 0]
    assert set(threads[0]) == {"thread_id", "subject", "latest_message_id", "message_count", "unread",
                               "unread_count", "latest_activity", "has_attachments"}
    assert threads[0]["subject"] == "Tur til skoven" and threads[0]["latest_message_id"] == 3012
    unread = read(SyntheticSite(), "messages", unread_only=True)["threads"]
    assert unread and all(thread["unread"] for thread in unread)


def test_a_thread_is_read_live_when_asked_and_never_stored(site, tmp_path):
    before = sorted(Path(tmp_path).rglob("*"))
    result = read(site, "message_thread", thread_id=301, last=2)
    assert result["stored"] is False and result["subject"] == "Tur til skoven"
    assert [message["message_id"] for message in result["messages"]] == [3011, 3012]
    assert result["messages"][0]["text"] == "Kære forældre\nOnsdag går vi i skoven. Husk regntøj."
    assert result["messages"][0]["sender"] == "Klasselærer (KL)" or result["messages"][0]["sender"]
    assert [message["message_id"] for message in read(SyntheticSite(), "message_thread", thread_id=301)[
        "messages"]] == [3012]
    assert sorted(Path(tmp_path).rglob("*")) == before


def test_a_message_without_a_thread_is_found_by_its_id_and_a_missing_thread_is_refused(site):
    assert [message["message_id"] for message in read(site, "message_thread", thread_id=950)["messages"]] == [950]
    with pytest.raises(CollectionError, match="thread 999"):
        read(SyntheticSite(), "message_thread", thread_id=999)


# --- The live school check -----------------------------------------------------------------------------

def test_the_live_school_check_prints_counts_only(site):
    result = read(site, "school_check")
    assert result["school"] == SCHOOL.digest() and result["week"] == "2026-W46"
    assert result["rules"]["weekly_plan.sections"]["matched"] > 0
    text = json.dumps(result, ensure_ascii=False)
    assert "skoven" not in text and "Gummistøvler" not in text and "Juleklip" not in text


# --- The CLI ------------------------------------------------------------------------------------------

@pytest.fixture
def home(monkeypatch, tmp_path, site):
    """A state directory with both files and a session, and the CLI's client on the synthetic site."""
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    (tmp_path / "school.toml").write_text(f'[site]\nhost = "{SCHOOL.host}"\n', encoding="utf-8")
    (tmp_path / "profile.toml").write_text(
        '[[children]]\nstudent_id = 9101\nname = "Alfa"\ncalendar_class_name = "001"\n\n'
        '[[children]]\nstudent_id = 9202\nname = "Beta"\ncalendar_class_name = "003"\n', encoding="utf-8")
    SessionStore().save({"cookies": []})
    monkeypatch.setattr(kit_cli, "school_client",
                        lambda state, host: ReadOnlyClient(host, transport=httpx.MockTransport(site.handle)))
    return tmp_path


def test_the_cli_prints_a_childs_schedule_as_json(home, capsys):
    assert kit_cli.main(["schedule", "--child", "Alfa"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["child"] == "Alfa" and result["week"] == "2026-W46" and result["lessons"]


def test_the_cli_needs_a_child_when_the_profile_has_several(home, capsys):
    assert kit_cli.main(["messages", "list"]) == 2
    assert "Choose --child: Alfa, Beta" in capsys.readouterr().err
    assert kit_cli.main(["messages", "list", "--child", "Gamma"]) == 2
    assert kit_cli.main(["messages", "list", "--child", "9202"]) == 0


def test_the_cli_prints_one_item_per_line_and_a_calendar_file(home, capsys):
    assert kit_cli.main(["messages", "list", "--child", "alfa", "--format", "ndjson"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["thread_id"] for line in lines] == [301, 302, 0] and lines[0]["child"] == "Alfa"
    assert kit_cli.main(["calendar", "--child", "Alfa", "--week", "47", "--format", "ics"]) == 0
    ics = capsys.readouterr().out
    assert ics.startswith("BEGIN:VCALENDAR") and ics.count("BEGIN:VEVENT") == 3
    assert "DTSTART;TZID=Europe/Copenhagen:20261117T" in ics


def test_the_cli_exits_3_without_a_session_or_when_it_has_expired(home, site, capsys):
    site.redirect_to_login(ALFA, "messages/conversations")
    assert kit_cli.main(["messages", "list", "--child", "Alfa"]) == 3
    assert "session has expired" in capsys.readouterr().err
    (home / "auth-state.json").unlink()
    assert kit_cli.main(["schedule", "--child", "Alfa"]) == 3
    assert "skolekit auth login" in capsys.readouterr().err


def test_the_cli_exits_4_when_the_site_answers_wrongly_and_2_for_a_bad_week(home, site, capsys):
    site.override(ALFA, "forms/open", lambda request: httpx.Response(200, text="<html></html>"))
    assert kit_cli.main(["forms", "--child", "Alfa"]) == 4
    assert "Forms payload missing" in capsys.readouterr().err
    assert kit_cli.main(["schedule", "--child", "Alfa", "--week", "next"]) == 2


def test_the_cli_refuses_without_the_schools_files(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    assert kit_cli.main(["schedule"]) == 2
    assert "school.toml is missing" in capsys.readouterr().err


def test_the_live_reads_write_nothing_to_the_state_directory(home):
    before = sorted(path.name for path in home.iterdir())
    for argv in (["schedule"], ["calendar"], ["weekly-plan"], ["forms"], ["notices"], ["messages", "list"],
                 ["messages", "read", "301", "--last", "2"], ["school", "check"]):
        assert kit_cli.main([*argv, "--child", "Alfa"]) == 0, argv
    assert sorted(path.name for path in home.iterdir()) == before


def test_ag_school_mounts_the_live_reads_with_the_households_defaults():
    parser = product_cli.build_parser()
    for argv in (["schedule", "--child", "Alfa"], ["calendar", "--all-classes"], ["forms", "--state", "open"],
                 ["notices", "--include-expired"], ["messages", "list", "--unread"],
                 ["messages", "read", "301", "--last", "3"]):
        assert callable(parser.parse_args(argv).handler)


def test_skolekit_writes_its_own_profile_without_the_product(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AG_SCHOOL_HOME", str(tmp_path))
    assert kit_cli.main(["profile", "write", "--host", "eksempel.m.skoleintra.dk",
                         "--child", "Alfa:9101:001", "--child", "Beta:9202:003"]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["profile.toml", "school.toml"]
    capsys.readouterr()
    assert kit_cli.main(["profile", "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["school"] == "eksempel.m.skoleintra.dk"
    assert [(child["name"], child["class"]) for child in shown["children"]] == [("Alfa", "1. klasse"),
                                                                                ("Beta", "3. klasse")]
    assert kit_cli.main(["profile", "write", "--host", "eksempel.m.skoleintra.dk", "--child", "Alfa:9101:001"]) == 2
    assert "already present" in capsys.readouterr().err
    assert kit_cli.main(["profile", "write", "--host", "eksempel.m.skoleintra.dk", "--child", "Alfa-9101"]) == 2
