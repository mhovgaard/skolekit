import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from skoleklar.runtime import scan as scan_service
from skoleklar.dashboard_composition import school_calendar
from skoleklar.dashboard_timeline import school_week, slice_starts, slice_state
from skoleklar.dashboard_timing import ZONE
from skoleklar.delivery_api import create_app
from skoleklar.delivery_store import LocalDeliveryStore
from skoleklar.display_store import (
    DisplayUnavailable,
    VersionChanged,
    current_bundle,
    make_timeline,
)
from tests_python.test_dashboard_rendering import weekday_grid_state


def local(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZONE)


def context_at(at, *, updated="2026-09-24T19:00:00"):
    source = local(updated)
    return {
        "now": at.isoformat(), "children": ["ChildA", "ChildB"], "items": [],
        "freshness": {"source_updated_at": source.isoformat(), "composed_at": at.isoformat(),
                      "expires_at": (source + timedelta(hours=36)).isoformat(), "status": "fresh"},
        "messages": {"checked": True, "bodies_retained": 0, "unread_count": 7,
                     "new_count": 0, "items": []},
    }


def composed_week(context):
    """A valid composition for the context's school week, as the model would return it."""
    calendar = school_calendar(context)
    value = weekday_grid_state(context["now"])
    value["freshness"] = dict(context["freshness"])
    value["week_label"] = calendar["week_label"]
    value["hero"].update(day=calendar["hero"]["day"], date=calendar["hero"]["date"],
                         children=[{"name": name, "lines": []} for name in context["children"]])
    value["weekend"] = []
    for day, entry in zip(calendar["weekdays"], value["weekdays"]):
        entry["date"], entry["day"] = day["date"], day["day"]
    value["messages"] = {"checked": True, "bodies_retained": 0, "unread_count": 7, "items": []}
    return value, {"facts": [], "source_updated_at": context["freshness"]["source_updated_at"]}


@pytest.mark.parametrize(("start", "expected"), [
    # Friday: the children's morning ends at 08:30; at 15:00 the weekend begins.
    ("2026-09-24T19:30:00", ["2026-09-24T19:30:00", "2026-09-25T00:00:00", "2026-09-25T08:30:00",
                             "2026-09-25T15:00:00", "2026-09-26T00:00:00"]),
    ("2026-09-25T19:30:00", ["2026-09-25T19:30:00", "2026-09-26T00:00:00",
                             "2026-09-26T18:00:00", "2026-09-27T00:00:00"]),
    # Sunday: noon gives way to Monday, whose view the children see 15:00–19:00.
    ("2026-09-26T19:30:00", ["2026-09-26T19:30:00", "2026-09-27T00:00:00",
                             "2026-09-27T12:00:00", "2026-09-27T15:00:00",
                             "2026-09-27T19:00:00", "2026-09-28T00:00:00"]),
])
def test_slices_start_at_each_layout_change_within_36_hours(start, expected):
    now = local(start)
    assert slice_starts(now, now + timedelta(hours=36)) == [local(value) for value in expected]


def test_slices_stop_when_the_data_expires():
    now = local("2026-09-24T05:45:00")
    assert slice_starts(now, local("2026-09-24T15:00:00")) == [now, local("2026-09-24T08:30:00")]
    assert slice_starts(now, local("2026-09-24T08:30:00")) == [now]


def test_after_cutoff_the_next_school_day_comes_from_its_weekday_row():
    week, _ = composed_week(context_at(local("2026-09-21T08:00:00"), updated="2026-09-21T07:00:00"))
    afternoon = slice_state(week, context_at(local("2026-09-21T15:00:00"), updated="2026-09-21T07:00:00"))
    assert afternoon["hero"]["day"] == "Tirsdag 22. sep."
    assert afternoon["hero"]["state"] == "Næste skoledag"
    assert afternoon["hero"]["children"] == week["weekdays"][1]["children"]
    assert afternoon["freshness"]["composed_at"] == local("2026-09-21T15:00:00").isoformat()
    midnight = slice_state(week, context_at(local("2026-09-22T00:00:00"), updated="2026-09-21T07:00:00"))
    assert midnight["hero"]["state"] == "I dag"
    morning = slice_state(week, context_at(local("2026-09-21T08:00:00"), updated="2026-09-21T07:00:00"))
    assert morning["hero"] == week["hero"]


def party(ref, child, day, thread):
    return {"ref": ref, "child": child, "effective_date": day, "end_date": None, "type": "event",
            "title": "Fødselsdagsfest", "summary": "", "actionability": "informational",
            "urgency": "routine", "confidence": 0.9, "needs_review": False,
            "private_message_fact": True, "grouped": False, "activity_kind": "birthday_party",
            "message_thread": thread}


def test_weekend_rows_follow_the_slice_refill_from_facts_and_other_weeks_are_refused():
    def at(text, items, updated="2026-09-19T09:00:00"):
        return {**context_at(local(text), updated=updated), "items": items}

    # Two Saturday parties fill both rows; Sunday's is counted as overflow.
    items = [party("fact-1", "ChildA", "2026-09-19", 1), party("fact-2", "ChildB", "2026-09-19", 2),
             party("fact-3", "ChildA", "2026-09-20", 3)]
    week, _ = composed_week(at("2026-09-19T10:00:00", items))
    saturday = slice_state(week, at("2026-09-19T10:00:00", items))
    assert [row["day"] for row in saturday["weekend"]] == ["Lør. 19. sep.", "Lør. 19. sep."]
    assert saturday["weekend_overflow"] == 1
    # At Sunday midnight Saturday is over: Sunday's party gets a row and nothing is left over.
    sunday = slice_state(week, at("2026-09-20T00:00:00", items))
    assert [(row["day"], row["who"], [entry["text"] for entry in row["lines"]]) for row in sunday["weekend"]] == [
        ("Søn. 20. sep.", "ChildA", ["Fødselsdagsfest"])]
    assert sunday["weekend_overflow"] == 0
    noon = slice_state(week, at("2026-09-20T12:00:00", items))
    assert noon["hero"] == week["hero"] and [row["day"] for row in noon["weekend"]] == ["Søn. 20. sep."]
    with pytest.raises(ValueError):
        slice_state(week, at("2026-09-25T15:00:00", items, updated="2026-09-25T09:00:00"))


def test_one_scan_composes_each_school_week_once_and_reuses_unchanged_weeks(monkeypatch, caplog):
    monkeypatch.setenv("AG_WEATHER", "off")
    caplog.set_level(logging.INFO, logger="agskoleintra.cloud_job")
    calls = []

    def cache_events():
        events = [json.loads(r.message) for r in caplog.records if '"composition_cache"' in r.message]
        caplog.clear()
        return [(event["week"], event["result"], event.get("reason")) for event in events]

    def compose_week(context):
        calls.append(context["now"])
        return composed_week(context)

    now = local("2026-09-24T19:30:00")
    stage = {"name": ""}
    frames, weeks, first = scan_service._dashboard_frames(now, context_at, compose_week, {}, "model", stage)
    assert [at for at, _ in frames] == slice_starts(now, now + timedelta(hours=36))
    assert calls == [now.isoformat(), local("2026-09-25T15:00:00").isoformat()]
    assert set(weeks) == {date(2026, 9, 21), date(2026, 9, 28)}
    assert [state["hero"]["day"] for _, state in frames] == [
        "Fredag 25. sep.", "Fredag 25. sep.", "Fredag 25. sep.", "Mandag 28. sep.", "Mandag 28. sep."]
    assert first["now"] == now.isoformat()
    assert cache_events() == [("2026-09-21", "miss", None), ("2026-09-28", "miss", None)]

    cached = {week["key"]: {"state": week["state"], "audit": week["audit"]} for week in weeks.values()}
    calls.clear()
    _, again, _ = scan_service._dashboard_frames(now, context_at, compose_week,
                                              json.loads(json.dumps(cached)), "model", stage)
    assert calls == [] and all(week["reused"] for week in again.values())
    assert cache_events() == [("2026-09-21", "reused", None), ("2026-09-28", "reused", None)]
    # A cached week that no longer validates is recomposed, and the log says why.
    stale = json.loads(json.dumps(cached))
    stale[weeks[date(2026, 9, 21)]["key"]]["state"]["hero"]["date"] = "2026-10-05"
    calls.clear()
    scan_service._dashboard_frames(now, context_at, compose_week, stale, "model", stage)
    assert calls == [now.isoformat()]
    assert cache_events()[0] == ("2026-09-21", "rejected", "dashboard dates must follow the selected school week")
    calls.clear()
    _, other_model, _ = scan_service._dashboard_frames(now, context_at, compose_week, cached, "other", stage)
    assert len(calls) == 2 and not any(week["reused"] for week in other_model.values())


def timeline_fixture(tmp_path):
    week, _ = composed_week(context_at(local("2026-09-21T08:00:00"), updated="2026-09-21T07:00:00"))
    afternoon = slice_state(week, context_at(local("2026-09-21T15:00:00"), updated="2026-09-21T07:00:00"))
    now = local("2026-09-21T08:00:00")
    timeline = make_timeline([(now, week), (now + timedelta(hours=1), week),
                              (local("2026-09-21T15:00:00"), afternoon)], "run-1", now=now)
    store = LocalDeliveryStore(tmp_path)
    store.write_json("current.json", timeline)
    return timeline, store


def test_timeline_merges_unchanged_moments_and_versions_follow_the_frame(tmp_path):
    timeline, store = timeline_fixture(tmp_path)
    first, second = timeline["frames"]
    assert first["metadata"]["version"] == "run-1-" + first["metadata"]["frame_sha256"][:12]
    assert first["metadata"]["valid_until"] == second["metadata"]["valid_from"] == "2026-09-21T13:00:00+00:00"
    assert second["metadata"]["valid_until"] == timeline["expires_at"]
    morning = current_bundle(store, now=datetime(2026, 9, 21, 9, tzinfo=UTC))
    assert morning["metadata"]["version"] == first["metadata"]["version"]
    assert morning["upcoming"] == [{key: second["metadata"][key] for key in
                                    ("version", "valid_from", "valid_until", "frame_sha256")}]
    ahead = current_bundle(store, now=datetime(2026, 9, 21, 9, tzinfo=UTC),
                           version=second["metadata"]["version"])
    assert ahead["metadata"]["frame_sha256"] == second["metadata"]["frame_sha256"]
    afternoon = current_bundle(store, now=datetime(2026, 9, 21, 14, tzinfo=UTC))
    assert afternoon["metadata"]["version"] == second["metadata"]["version"] and afternoon["upcoming"] == []
    with pytest.raises(VersionChanged):
        current_bundle(store, now=datetime(2026, 9, 21, 14, tzinfo=UTC), version=first["metadata"]["version"])
    with pytest.raises(DisplayUnavailable):
        current_bundle(store, now=datetime.fromisoformat(timeline["expires_at"]))


def test_device_status_lists_upcoming_frames_within_the_firmware_limit(tmp_path):
    timeline, store = timeline_fixture(tmp_path)
    first, second = timeline["frames"]
    registry = [{"device_id": "firebeetle-ag", "token_hash": hashlib.sha256(b"display").hexdigest(),
                 "scopes": ["display"]}]
    clock = {"now": datetime(2026, 9, 21, 9, tzinfo=UTC)}
    client = TestClient(create_app(store=LocalDeliveryStore(tmp_path / "receipts"), display_store=store,
                                   device_registry=registry, now=lambda: clock["now"]))
    headers = {"Authorization": "Bearer display"}
    status = client.get("/v1/display/status", headers=headers).json()
    assert status["metadata"]["version"] == first["metadata"]["version"]
    assert status["metadata"]["valid_until"] == second["metadata"]["valid_from"]
    assert status["next_change_at"] == second["metadata"]["valid_from"]
    assert [entry["version"] for entry in status["upcoming"]] == [second["metadata"]["version"]]
    upcoming = client.get("/v1/display/frame?version=" + second["metadata"]["version"], headers=headers)
    assert upcoming.status_code == 200
    assert hashlib.sha256(upcoming.content).hexdigest() == second["metadata"]["frame_sha256"]
    assert client.get("/v1/display/frame?version=old", headers=headers).status_code == 409
    clock["now"] = datetime(2026, 9, 21, 14, tzinfo=UTC)
    assert client.get("/v1/display/frame?version=" + first["metadata"]["version"],
                      headers=headers).status_code == 409

    # The device reads status into a 4096-byte buffer; a long timeline must still fit.
    start = datetime.fromisoformat(second["metadata"]["valid_from"])
    for index in range(5):
        extra = json.loads(json.dumps(second))
        extra["metadata"]["version"] = f"agskoleintra-sync-abcde-{index:012d}"
        extra["metadata"]["valid_from"] = (start + timedelta(hours=index + 1)).isoformat()
        timeline["frames"][-1]["metadata"]["valid_until"] = extra["metadata"]["valid_from"]
        timeline["frames"].append(extra)
    store.write_json("current.json", timeline)
    clock["now"] = datetime(2026, 9, 21, 9, tzinfo=UTC)
    response = client.get("/v1/display/status", headers=headers)
    assert len(response.json()["upcoming"]) == 6
    assert len(response.content) < 4096
