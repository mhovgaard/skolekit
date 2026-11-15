import copy
import json
from datetime import date, timedelta

import pytest

from skoleklar.runtime import scan as scan_service
from skoleklar.dashboard_renderer import render_html, render_png, validate
from skoleklar.day_summaries import (
    DaySummaryInvalid,
    attach_day_summaries,
    generate_day_summaries,
    plan_document,
    summarize_plan,
    summary_lookup,
    validate_output,
)
from skoleklar.triage.derivation import OpenRouterHTTPError
from skolekit.records import SourceCheck, SourceRecord
from skoleklar.storage import SnapshotStore
from tests_python.test_dashboard_rendering import weekday_grid_state

WEEKDAYS = ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag")


def plan(week=39, year=2026, *, blocks=None, general=None, class_code="00"):
    """A normalized weekly plan as the collector stores it."""
    monday = date.fromisocalendar(year, week, 1)
    blocks = {0: [("Dansk", "Vi arbejder med bogstavet\nE/e 🕷️")],
              1: [("Uden angivelse af fag", "Morgenstund\n- 10-mad\n- Idræt")]} if blocks is None else blocks
    days = []
    for offset, name in enumerate(WEEKDAYS):
        days.append({
            "weekday": name, "weekday_number": offset + 1,
            "date": f"{(monday + timedelta(days=offset)).isoformat()}T00:00:00",
            "plans": [{"subject": subject, "content_text": text, "content_html": text}
                      for subject, text in blocks.get(offset, [])],
        })
    general = [("Uden angivelse af fag", "Kære forældre\nTirsdag går vi i skoven.")] \
        if general is None else general
    return {
        "schema_version": 1,
        "week": {"number": week, "year": year, "label": f"{week}-{year}", "class_or_group": class_code},
        "general_plans": [{"subject": subject, "content_text": text} for subject, text in general],
        "days": days, "attachments": [],
    }


def store_plans(store, *plans_by_child):
    run = store.begin_run()
    checks = []
    for student_id, (child, plans) in enumerate(plans_by_child, 1):
        records = [SourceRecord(
            student_id=student_id, child=child, source_type="weekly_plan",
            source_key=f"class:{value['week']['number']}-{value['week']['year']}",
            source_url=f"https://example.invalid/{child}/{value['week']['number']}",
            title=value["week"]["label"], body_text=json.dumps(value["days"]),
            source_data=value, content_data={key: value[key] for key in ("week", "general_plans", "days")},
            raw_payload=value) for value in plans]
        checks.append(SourceCheck(student_id=student_id, child=child, source_type="weekly_plan",
                                  records=records, coverage={"kind": "record_keys",
                                                             "source_keys": [r.source_key for r in records]}))
    store.store_checks(run, checks)
    return run


def answer(document, **overrides):
    days = []
    for day in document["days"]:
        if day["blocks"]:
            days.append({"date": day["date"], "headline": f"Dag {day['date'][-2:]}",
                         "text": "Du skal lære noget nyt og lege med klassen.", "general_used": False})
        else:
            days.append({"date": day["date"], "headline": None, "text": None, "general_used": False})
    for date_value, values in overrides.items():
        next(day for day in days if day["date"] == date_value).update(values)
    return {"days": days}


class FakeClient:
    def __init__(self, responses=None):
        self.documents = []
        self.responses = list(responses or [])

    def generate(self, document, *, manifest, max_output_tokens):
        assert manifest.manifest_id == "day-summary-v1"
        self.documents.append(copy.deepcopy(document))
        output = self.responses.pop(0)(document) if self.responses else answer(document)
        if isinstance(output, Exception):
            raise output
        return {"response_id": f"r{len(self.documents)}", "output": output,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.001}}


def test_plan_document_keeps_plan_text_and_drops_ids_links_and_unnamed_subjects():
    document = plan_document(plan())
    assert document["class_level"] == "0. klasse"
    assert document["week"] == {"number": 39, "year": 2026}
    assert [day["date"] for day in document["days"]] == [
        "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
    assert document["days"][0]["blocks"] == [{"subject": "Dansk", "text": "Vi arbejder med bogstavet\nE/e 🕷️"}]
    assert document["days"][1]["blocks"][0]["subject"] is None
    assert document["general"] == [{"subject": None, "text": "Kære forældre\nTirsdag går vi i skoven."}]
    assert document["days"][2]["blocks"] == []
    assert set(json.dumps(document)) .isdisjoint({"\x00"})
    assert "content_html" not in json.dumps(document) and "https" not in json.dumps(document)


def test_validation_cleans_text_and_enforces_the_contract():
    document = plan_document(plan())
    output = answer(document, **{"2026-09-21": {"headline": "Bogstavet E 🕷️",
                                                "text": "Du skal  lære\nbogstavet E. 🕷️"}})
    days = validate_output(document, output)
    assert days[0] == {"date": "2026-09-21", "headline": "Bogstavet E",
                       "text": "Du skal lære bogstavet E.", "general_used": False}
    assert days[2]["text"] is None
    # An empty day may use a matching general note.
    with_general = answer(document, **{"2026-09-23": {"headline": "Skov", "text": "Klassen går i skoven.",
                                                      "general_used": True}})
    assert validate_output(document, with_general)[2]["general_used"] is True

    broken = {
        "day summaries must cover every plan day in order": {"days": answer(document)["days"][:4]},
        "a day with plan content needs a summary": answer(
            document, **{"2026-09-21": {"headline": None, "text": None}}),
        "headline and text must both be set or both be null": answer(
            document, **{"2026-09-21": {"headline": None}}),
        "day summary must not use relative time words": answer(
            document, **{"2026-09-21": {"text": "I dag skal du lære E."}}),
        "day summary is too long": answer(document, **{"2026-09-21": {"text": "a" * 481}}),
    }
    for message, value in broken.items():
        with pytest.raises(DaySummaryInvalid, match=message):
            validate_output(document, value)


def test_summarize_retries_with_fixed_feedback():
    client = FakeClient([
        lambda document: answer(document, **{"2026-09-22": {"text": "I morgen leger I."}}),
        lambda document: answer(document),
    ])
    result = summarize_plan(plan(), client)
    assert result["attempts"] == 2 and result["response_id"] == "r2"
    assert "validation_feedback" not in client.documents[0]
    assert "relative time words" in client.documents[1]["validation_feedback"]
    assert result["usage"]["total_tokens"] == 30


def test_generation_is_cached_per_plan_version_and_failures_retry(tmp_path):
    with SnapshotStore(tmp_path / "school.db") as store:
        store_plans(store, ("ChildA", [plan(39), plan(40)]), ("ChildB", [plan(39, class_code="02")]))
        client = FakeClient()
        result = generate_day_summaries(store, client, model="m", today=date(2026, 9, 23))
        assert (result["generated_count"], result["reused_count"], result["failed_count"]) == (3, 0, 0)
        assert {document["class_level"] for document in client.documents} == {"0. klasse", "2. klasse"}

        again = generate_day_summaries(store, FakeClient(), model="m", today=date(2026, 9, 23))
        assert (again["generated_count"], again["reused_count"]) == (0, 3)
        # Another model or a changed plan is summarized again; the rest are reused.
        assert generate_day_summaries(store, FakeClient(), model="other",
                                      today=date(2026, 9, 23), dry_run=True)["skipped_count"] == 3
        changed = plan(39, blocks={0: [("Dansk", "Nyt emne: bogstavet I")]})
        store_plans(store, ("ChildA", [changed]))
        failing = FakeClient([lambda document: {"days": []}] * 3)
        failed = generate_day_summaries(store, failing, model="m", today=date(2026, 9, 23))
        assert failed["failures"] == ["day summaries must cover every plan day in order"]
        assert failed["generated_count"] == 0 and failed["reused_count"] == 2
        retried = generate_day_summaries(store, FakeClient(), model="m", today=date(2026, 9, 23))
        assert retried["generated_count"] == 1
        rows = store.connection.execute(
            "SELECT status, count(*) FROM day_summary_runs GROUP BY status").fetchall()
        assert {row[0]: row[1] for row in rows} == {"complete": 4}


def test_authentication_errors_stop_generation_and_other_errors_do_not(tmp_path):
    with SnapshotStore(tmp_path / "school.db") as store:
        store_plans(store, ("ChildA", [plan(39), plan(40)]))
        busy = FakeClient([lambda document: OpenRouterHTTPError(503, "busy")])
        result = generate_day_summaries(store, busy, model="m", today=date(2026, 9, 23))
        assert result["failures"] == ["provider_http_503"] and result["generated_count"] == 1
        with pytest.raises(OpenRouterHTTPError):
            generate_day_summaries(store, FakeClient([lambda document: OpenRouterHTTPError(401, "key")]),
                                   model="m", today=date(2026, 9, 23))


def test_limit_bounds_model_calls_per_run(tmp_path):
    with SnapshotStore(tmp_path / "school.db") as store:
        store_plans(store, ("ChildA", [plan(39), plan(40)]), ("ChildB", [plan(39), plan(40)]))
        client = FakeClient()
        result = generate_day_summaries(store, client, model="m", today=date(2026, 9, 23), limit=1)
        assert len(client.documents) == 1 and result["skipped_count"] == 3
        # Siblings with the same plan text still get a summary each.
        generate_day_summaries(store, FakeClient(), model="m", today=date(2026, 9, 23))
        lookup = summary_lookup(store)
        assert {lookup(child, "2026-09-21")["status"] for child in ("ChildA", "ChildB")} == {"ok"}


def test_lookup_reports_each_status_and_the_state_carries_one_entry_per_day(tmp_path):
    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        store_plans(store, ("ChildA", [plan(39)]), ("ChildB", [plan(39, blocks={0: [("Matematik", "Tallet 5")]})]))
        generate_day_summaries(store, FakeClient(), model="m", today=date(2026, 9, 23))
        # ChildB's plan changes after its summary: the old text is not shown for it.
        store_plans(store, ("ChildA", [plan(39)]),
                    ("ChildB", [plan(39, blocks={0: [("Matematik", "Tallet 6")]})]))
    with SnapshotStore(database, read_only=True) as store:
        lookup = summary_lookup(store)
        assert lookup("ChildA", "2026-09-21")["status"] == "ok"
        assert lookup("ChildA", "2026-09-23") == {"status": "no_content", "headline": None, "text": None}
        assert lookup("ChildB", "2026-09-21")["status"] == "unavailable"
        assert lookup("ChildA", "2026-09-28")["status"] == "not_published"

        state = attach_day_summaries(weekday_grid_state(), lookup)
    validate(state)
    assert [day["date"] for day in state["day_summaries"]] == [day["date"] for day in state["weekdays"]]
    assert [child["name"] for child in state["day_summaries"][0]["children"]] == ["ChildA", "ChildB"]
    assert state["day_summaries"][0]["children"][0]["text"] == "Du skal lære noget nyt og lege med klassen."
    # The parents' board shows the next school day's headline as its plan line.
    headline = state["day_summaries"][0]["children"][0]["headline"]
    without = weekday_grid_state()
    assert headline in render_html(state) and headline not in render_html(without)
    assert render_png(state).tobytes() != render_png(without).tobytes()


def test_renderer_allowlist_rejects_malformed_day_summaries():
    state = weekday_grid_state()
    entry = {"name": "ChildA", "status": "ok", "headline": "Bogstavet E", "text": "Du lærer E."}
    other = {"name": "ChildB", "status": "not_published", "headline": None, "text": None}
    state["day_summaries"] = [{"date": "2026-09-21", "children": [entry, other]}]
    validate(state)
    for change in (
        lambda value: value[0]["children"][1].update(text="Skjult tekst"),
        lambda value: value[0]["children"][0].update(status="maybe"),
        lambda value: value[0].update(date="2026-09-26"),
        lambda value: value[0]["children"].reverse(),
        lambda value: value[0]["children"][0].update(text="Linje\nto"),
        lambda value: value[0]["children"][0].update(source="url"),
    ):
        broken = copy.deepcopy(state)
        change(broken["day_summaries"])
        with pytest.raises(ValueError):
            validate(broken)


def test_cloud_extras_attach_summaries_and_never_break_the_board(tmp_path):
    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        store_plans(store, ("ChildA", [plan(39)]))
        generate_day_summaries(store, FakeClient(), model="m", today=date(2026, 9, 23))
    with SnapshotStore(database, read_only=True) as store:
        extras = scan_service._day_summary_extras(store)
        state = extras(weekday_grid_state())
        assert state["day_summaries"][0]["children"][0]["status"] == "ok"
        assert state["day_summaries"][0]["children"][1]["status"] == "not_published"
        broken = weekday_grid_state()
        broken["weekdays"][0]["date"] = "not-a-date"
        assert "day_summaries" not in extras(broken)


def test_cli_shows_a_week_without_model_calls(tmp_path, capsys):
    from skoleklar.cli import build_parser
    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        store_plans(store, ("ChildA", [plan(39)]))
        generate_day_summaries(store, FakeClient(), model="m", today=date(2026, 9, 23))
    args = build_parser().parse_args(
        ["day-summaries", "--db", str(database), "--show", "2026-09-24", "--message-mode", "off"])
    assert args.handler(args) == 0
    shown = json.loads(capsys.readouterr().out)["day_summaries"]
    assert [day["date"] for day in shown][0] == "2026-09-21"
    assert shown[0]["children"][0]["status"] == "ok"
    assert shown[2]["children"][0]["status"] == "no_content"


def test_cloud_stage_logs_counts_and_a_failure_never_stops_the_sync(tmp_path, monkeypatch, caplog):
    import logging
    from skoleklar import settings as credentials
    from skoleklar.triage import derivation
    database = tmp_path / "school.db"
    with SnapshotStore(database) as store:
        store_plans(store, ("ChildA", [plan(39)]))
    monkeypatch.setattr(credentials, "openrouter_api_key", lambda: "test")
    monkeypatch.setattr(derivation, "OpenRouterClient", lambda **_: FakeClient())

    def events():
        found = [json.loads(r.message) for r in caplog.records if "day_summar" in r.message]
        caplog.clear()
        return [event for event in found if event["event"] != "stage_started"]

    with caplog.at_level(logging.INFO, logger="agskoleintra.cloud_job"):
        scan_service._generate_day_summaries(database, model="m", message_mode="events-only",
                                          today=date(2026, 9, 23))
        [done] = events()
        assert done["event"] == "stage_completed" and done["generated_count"] == 1
        with SnapshotStore(database) as store:
            store_plans(store, ("ChildA", [plan(39, blocks={0: [("Dansk", "Nyt")]})]))
        monkeypatch.setattr(derivation, "OpenRouterClient",
                            lambda **_: FakeClient([lambda d: OpenRouterHTTPError(401, "key")]))
        scan_service._generate_day_summaries(database, model="m", message_mode="events-only",
                                          today=date(2026, 9, 23))
        [failed] = events()
        assert failed == {"event": "day_summaries_failed", "error_type": "OpenRouterHTTPError",
                          "duration_seconds": failed["duration_seconds"]}
