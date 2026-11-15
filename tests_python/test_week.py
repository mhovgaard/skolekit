"""Phase 8: the validated week view, from the household's checked records.

The facts the board stands on, with the review verdicts applied, the message
facts and the bookings, for a span of days; no evidence, links or handles.
"""
import json
from datetime import date

import pytest

from skoleklar import cli, week
from skoleklar.storage import SnapshotStore
from skolekit.profile import SiteProfile
from tests_python.synthetic_household import SCHOOL
from tests_python.test_school_wording import children, scanned


def fact(key, child, day, **extra):
    return {"stable_key": key, "child": child, "item_type": "event", "title": key, "summary": f"{key} summary",
            "effective_date": day, "end_date": None, "all_day": True, "actionability": "informational",
            "urgency": "routine", "needs_review": False, "source_types": ["frontpage"],
            "source_urls": ["https://example.invalid/private"], "evidence": [{"quote": "x"}],
            "message_thread_ref": None, **extra}


ITEMS = [
    fact("trip", "Alfa", "2026-11-11"),
    fact("party", "Beta", "2026-11-13", source_types=["messages"], message_thread_ref="thr_synthetic"),
    fact("household-meeting", "household", "2026-11-12"),
    fact("long-week", "Alfa", "2026-11-02", end_date="2026-11-09"),
    fact("next-month", "Alfa", "2026-12-10"),
    fact("routine", "Alfa", None),
    fact("dismissed", "Alfa", "2026-11-10", dismissed_at="2026-11-08T10:00:00Z"),
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(week, "delivery_items_from_snapshot", lambda store, message_mode: [dict(i) for i in ITEMS])
    # A booked form for 9 November 15:00 (Beta) and the typed tables, as the scan leaves them.
    database, _ = scanned(tmp_path / "s.db", SiteProfile(SCHOOL, children(SCHOOL)))
    database.close()
    with SnapshotStore(tmp_path / "s.db", read_only=True) as opened:
        yield opened


def test_the_week_holds_the_facts_and_bookings_of_its_days(store):
    view = week.week_view(store, start=date(2026, 11, 9))
    assert (view["from"], view["to"], view["source"]) == ("2026-11-09", "2026-11-15", "store")
    assert [f["stable_key"] for f in view["facts"]] == ["long-week", "trip", "household-meeting", "party"]
    assert [f["from"] for f in view["facts"]] == ["school", "school", "school", "messages"]
    assert [(b["child"], b["state"], b["starts_local"]) for b in view["bookings"]] == [
        ("Beta", "booked", "2026-11-09T15:00")]
    assert view["as_of"]


def test_the_week_says_what_happens_not_where_from(store):
    text = json.dumps(week.week_view(store, start=date(2026, 11, 9)))
    assert "example.invalid" not in text and "quote" not in text and "thr_synthetic" not in text


def test_one_childs_week_keeps_the_households_facts(store):
    view = week.week_view(store, start=date(2026, 11, 9), child="Alfa", include_undated=True)
    assert [f["stable_key"] for f in view["facts"]] == ["long-week", "trip", "household-meeting", "routine"]
    assert view["bookings"] == []


def test_a_shown_verdict_keeps_its_note_and_a_dropped_fact_is_left_out(store, monkeypatch):
    flagged = [fact("checked", "Alfa", "2026-11-10", needs_review=True),
               fact("wrong-class", "Alfa", "2026-11-10", needs_review=True)]
    monkeypatch.setattr(week, "delivery_items_from_snapshot", lambda store, message_mode: flagged)
    monkeypatch.setattr(week, "apply_verdicts", lambda store, items: [
        {**items[0], "needs_review": False, "title": "checked · for both classes"}])
    view = week.week_view(store, start=date(2026, 11, 9))
    assert [(f["stable_key"], f["title"], f["needs_review"]) for f in view["facts"]] == [
        ("checked", "checked · for both classes", False)]


def test_skoleklar_week_prints_the_view_from_a_copy(store, tmp_path, capsys):
    args = cli.build_parser().parse_args(["week", "--from", "2026-11-09", "--db", str(tmp_path / "s.db")])
    assert args.handler(args) == 0
    assert [f["stable_key"] for f in json.loads(capsys.readouterr().out)["facts"]][:2] == ["long-week", "trip"]
    bad = cli.build_parser().parse_args(["week", "--days", "40", "--db", str(tmp_path / "s.db")])
    assert bad.handler(bad) == 2


def test_reading_a_copy_leaves_its_folder_as_it_was(tmp_path, monkeypatch, capsys):
    # A pulled copy is in WAL mode with nothing beside it; a plain read-only open would add -wal and -shm.
    monkeypatch.setattr(week, "delivery_items_from_snapshot", lambda store, message_mode: [])
    folder = tmp_path / "cloud"
    folder.mkdir()
    database, _ = scanned(folder / "copy.db", SiteProfile(SCHOOL, children(SCHOOL)))
    database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database.close()
    for suffix in ("-wal", "-shm"):
        (folder / f"copy.db{suffix}").unlink(missing_ok=True)
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    args = cli.build_parser().parse_args(["week", "--from", "2026-11-09", "--db", str(folder / "copy.db")])
    assert args.handler(args) == 0
    assert json.loads(capsys.readouterr().out)["bookings"]
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before
