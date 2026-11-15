import json
from datetime import datetime

import pytest

from skoleklar.dashboard_events import ACTIVITY_LABELS, birthday_notice, confirmed_activity, event_confirmations
from skoleklar.dashboard_validation import validate_composition
from skoleklar.dashboard_timing import dashboard_weekend_dates
from tests_python.test_dashboard_rendering import weekday_grid_state

KEY = "message-event:" + "a" * 64


def confirmation():
    return {"schema_version": 1, "events": [{"stable_key": KEY, "child": "ChildA",
             "date": "2026-10-25", "kind": "class_trip"}]}


def weekend_fixture():
    state = weekday_grid_state("2026-10-24T14:00:00+02:00")
    state["hero"]["day"] = "Mandag 26. okt."
    for i, day in enumerate(state["weekdays"]):
        day.update(date=f"2026-10-{26+i}", day=f"{26+i}. okt.")
    state["weekend"] = [{"day": "Søn. 25. okt.", "text": "ChildA: Klassetur"}]
    facts = [
        {"ref": "birthday", "type": "event", "child": "ChildA", "effective_date": "2026-10-25",
         "title": "Alex har fødselsdag", "summary": "Alex har fødselsdag den 25. oktober.",
         "actionability": "informational", "private_message_fact": False},
        {"ref": "trip", "type": "event", "child": "ChildA", "effective_date": "2026-10-25",
         "title": "Klassetur", "private_message_fact": True, "confirmed_activity_kind": "class_trip"},
    ]
    context = {"now": state["freshness"]["composed_at"], "items": facts,
               "children": ["ChildA", "ChildB"], "freshness": state["freshness"],
               "messages": state["messages"]}
    audit = {"source_updated_at": state["freshness"]["source_updated_at"], "facts": [
        {"ref": "birthday", "decision": "omitted", "reason": "Birth date only"},
        {"ref": "trip", "decision": "displayed", "location": "weekend[0].text", "reason": "Confirmed trip"},
    ]}
    return context, state, audit


def test_classmate_birth_date_is_not_an_invitation():
    fact = weekend_fixture()[0]["items"][0]
    assert birthday_notice(fact)
    fact["summary"] = "Invitation til fødselsdagsfest søndag."
    assert not birthday_notice(fact)


def test_confirmations_are_categorical_and_bound_to_existing_event_child_and_date():
    entries = event_confirmations(confirmation())
    item = {"stable_key": KEY, "type": "event", "child": "ChildA", "effective_date": "2026-10-25"}
    assert confirmed_activity(item, entries) == "class_trip"
    assert confirmed_activity({**item, "child": "ChildB"}, entries) is None
    assert confirmed_activity({**item, "effective_date": "2026-11-01"}, entries) is None
    for field, value in (("body", "private prose"), ("kind", "private narrative")):
        doc = confirmation()
        doc["events"][0][field] = value
        with pytest.raises(ValueError):
            event_confirmations(doc)


def test_export_applies_owner_label_without_retaining_identifiers_or_changing_sources(tmp_path, monkeypatch):
    from skoleklar import dashboard_context
    from skolekit.records import SourceCheck
    from skoleklar.storage import SnapshotStore

    context, state, _ = weekend_fixture()
    trip = {**context["items"][1], "stable_key": KEY, "summary": "Generic event",
            "end_date": None, "actionability": "informational", "urgency": "attention",
            "confidence": .98, "needs_review": True}
    birthday = {**context["items"][0], "stable_key": "calendar-birthday",
                "end_date": "2026-10-25", "urgency": "routine", "confidence": .99,
                "needs_review": False}
    trip["title"] = "School event"
    monkeypatch.setattr(dashboard_context, "delivery_items_from_snapshot", lambda *a, **k: [])
    monkeypatch.setattr(dashboard_context, "build_brief_range", lambda *a, **k: {
        "items": [birthday, trip], "atomic_item_count": 2})
    path = tmp_path / "restricted.db"
    with SnapshotStore(path) as store:
        run = store.begin_run()
        store.store_checks(run, [SourceCheck(student_id=1, child="ChildA", source_type="calendar",
                                            records=[], complete=True)])
    before = path.read_bytes()
    with SnapshotStore(path, read_only=True) as store:
        result = dashboard_context.export_context(store,
            now=datetime.fromisoformat(state["freshness"]["composed_at"]), confirmations=confirmation())
    assert result["items"][0]["birthday_notice"] is True
    assert result["items"][1]["title"] == "Klassetur"
    assert result["items"][1]["confirmed_activity_kind"] == "class_trip"
    assert result["items"][1]["needs_review"] is True
    assert "message-event:" not in json.dumps(result)
    assert trip["title"] == "School event" and path.read_bytes() == before


def test_current_weekend_excludes_the_weekend_closing_mondays_school_week():
    dates = dashboard_weekend_dates(datetime.fromisoformat("2026-10-24T14:00:00+02:00"))
    assert [d.isoformat() for d in dates] == ["2026-10-24", "2026-10-25"]
    context, state, audit = weekend_fixture()
    state["weekend"][0]["day"] = "Søn. 1. nov."
    with pytest.raises(ValueError, match="current upcoming weekend"):
        validate_composition(context, state, audit)


def test_weekend_fact_must_match_its_specific_row_date():
    context, state, audit = weekend_fixture()
    state["weekend"][0]["day"] = "Lør. 24. okt."
    with pytest.raises(ValueError, match="fact date must match"):
        validate_composition(context, state, audit)


def test_trip_is_displayed_and_birthday_notice_cannot_replace_it():
    context, state, audit = weekend_fixture()
    validate_composition(context, state, audit)
    state["weekend"][0]["text"] = "ChildA: Fødselsdag"
    audit["facts"][0]["reason"] = "Birth date only"
    audit["facts"][1].update(decision="omitted", reason="Space")
    with pytest.raises(ValueError, match="confirmed weekend activity must be displayed"):
        validate_composition(context, state, audit)


def test_classmate_birth_date_is_never_displayed():
    context, state, audit = weekend_fixture()
    state["weekend"].append({"day": "Søn. 25. okt.", "text": "ChildA: Alex fødselsdag"})
    audit["facts"][0].update(decision="displayed", location="weekend[1].text")
    with pytest.raises(ValueError, match="birthday notice is not"):
        validate_composition(context, state, audit)


def test_weekend_activity_reads_as_an_event_not_a_message():
    context, state, audit = weekend_fixture()
    state["weekend"][0]["text"] = "ChildA: Besked: Klassetur"
    with pytest.raises(ValueError, match="weekend activity must read as an event"):
        validate_composition(context, state, audit)


@pytest.mark.parametrize("kind", ["birthday_party", "social_activity", "class_trip"])
def test_extracted_weekend_activity_must_be_displayed(kind):
    context, state, audit = weekend_fixture()
    trip = context["items"][1]
    del trip["confirmed_activity_kind"]
    trip.update(activity_kind=kind, title=ACTIVITY_LABELS[kind])
    state["weekend"][0]["text"] = {
        "birthday_party": "ChildA: Fødselsdagsfest",
        "social_activity": "ChildA: Socialt arrangement",
        "class_trip": "ChildA: Klassetur",
    }[kind]
    validate_composition(context, state, audit)
    if kind == "birthday_party":
        state["weekend"][0]["text"] = "ChildA: Skoleevent"
        with pytest.raises(ValueError, match="birthday party must retain"):
            validate_composition(context, state, audit)
    state["weekend"] = []
    audit["facts"][1].update(decision="omitted", reason="Space")
    audit["facts"][1].pop("location")
    with pytest.raises(ValueError, match="confirmed weekend activity must be displayed"):
        validate_composition(context, state, audit)


def test_export_carries_extracted_activity_kind(tmp_path, monkeypatch):
    from skoleklar import dashboard_context
    from skolekit.records import SourceCheck
    from skoleklar.storage import SnapshotStore

    context, state, _ = weekend_fixture()
    party = {**context["items"][1], "stable_key": KEY, "summary": "Generic event",
             "title": "Birthday party", "activity_kind": "birthday_party",
             "end_date": None, "actionability": "informational", "urgency": "attention",
             "confidence": .98, "needs_review": True}
    del party["confirmed_activity_kind"]
    monkeypatch.setattr(dashboard_context, "delivery_items_from_snapshot", lambda *a, **k: [])
    monkeypatch.setattr(dashboard_context, "build_brief_range", lambda *a, **k: {
        "items": [party], "atomic_item_count": 1})
    path = tmp_path / "restricted.db"
    with SnapshotStore(path) as store:
        run = store.begin_run()
        store.store_checks(run, [SourceCheck(student_id=1, child="ChildA", source_type="calendar",
                                            records=[], complete=True)])
    with SnapshotStore(path, read_only=True) as store:
        result = dashboard_context.export_context(
            store, now=datetime.fromisoformat(state["freshness"]["composed_at"]))
    assert result["items"][0]["activity_kind"] == "birthday_party"
    assert result["items"][0]["title"] == "Fødselsdagsfest"
    assert "confirmed_activity_kind" not in result["items"][0]


@pytest.mark.parametrize("omit", [True, False])
def test_confirmed_trip_cannot_be_omitted_or_renamed_to_another_activity(omit):
    context, state, audit = weekend_fixture()
    if omit:
        state["weekend"] = []
        audit["facts"][1]["decision"] = "omitted"
    else:
        state["weekend"][0]["text"] = "ChildA: Fødselsdag"
    with pytest.raises(ValueError, match="confirmed (weekend activity|class trip)"):
        validate_composition(context, state, audit)
