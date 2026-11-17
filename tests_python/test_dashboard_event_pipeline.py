"""Weekend activities from stored message facts through the real brief, context,
validation and both renderers. Synthetic facts only; no school data or network."""
from datetime import date, datetime

import pytest

from skoleklar.brief import build_brief_range
from skoleklar.dashboard_composition import restamp
from skoleklar.dashboard_context import export_context
from skoleklar.dashboard_renderer import render_html, render_png
from skoleklar.dashboard_validation import validate_composition
from skoleklar.delivery_manifest import delivery_items_from_snapshot
from skoleklar.restricted_messages import MESSAGE_POLICY_VERSION
from skolekit.records import SourceCheck
from skoleklar.storage import SnapshotStore
from tests_python.test_dashboard_events import weekend_fixture
from tests_python.test_weather import weather_state

# This refactor's core set: `pytest -m pipeline` (docs/maintenance.md).
pytestmark = pytest.mark.pipeline

NOW = datetime.fromisoformat("2026-10-24T14:00:00+02:00")  # Saturday: the weekend cover.
SATURDAY, SUNDAY = "2026-10-24", "2026-10-25"
LABEL = {SATURDAY: "Lør. 24. okt.", SUNDAY: "Søn. 25. okt."}


def fact(number, *, child="ChildA", day=SUNDAY, kind="birthday_party", event_kind="event", cancelled=False,
         celebrant=None):
    """A stored message fact in the restricted privacy schema."""
    return {"stable_key": f"message-event:{number:064x}", "child": child, "event_kind": event_kind,
            "activity_kind": kind if event_kind == "event" else None, "celebrant": celebrant,
            "effective_date": day,
            "end_date": None, "all_day": True,
            "cancellation_state": "cancelled" if cancelled or event_kind == "cancellation" else "active",
            "necessary_location": None, "bring_items": [], "requirement": None,
            "participant_display_names": [], "template_key": f"private_{event_kind}",
            "confidence": .95, "needs_review": False,
            "workflow_state": "cancelled" if event_kind == "cancellation" else "active"}


def export(tmp_path, threads):
    """Store each thread's facts, then export the dashboard context the cloud job composes from."""
    path = tmp_path / "restricted.db"
    with SnapshotStore(path) as store:
        run = store.begin_run()
        store.store_checks(run, [SourceCheck(student_id=index, child=child, source_type="calendar",
                                             records=[], complete=True)
                                 for index, child in enumerate(("ChildA", "ChildB"), 1)])
        for letter, facts in threads.items():
            store.complete_message_event_extraction(
                thread_ref="thr_" + letter * 16, message_ref="msg_" + letter * 16, facts=facts,
                extractor_version="message-events-v4", policy_version=MESSAGE_POLICY_VERSION)
    with SnapshotStore(path, read_only=True) as store:
        items = delivery_items_from_snapshot(store, message_mode="events-only")
        return items, export_context(store, now=NOW)


def compose(context, rows, placements, *, overflow=0):
    """A controlled composition: `rows` of (date, who, [(text, refs)]), each line drawn from its facts
    (a ref ending in '!' is shown as cancelled); `placements` maps each fact ref to a location."""
    from skoleklar.present import line
    _, template, _ = weekend_fixture()
    state = restamp(template, context)
    facts = {item["ref"]: item for item in context["items"]}

    def board_line(day, text, names):
        shown = [{**facts[name.rstrip("!")], "cancelled": True} if name.endswith("!") else facts[name]
                 for name in names]
        return line(text, [name.rstrip("!") for name in names], shown, day=date.fromisoformat(day))

    state["weekend"] = [{"date": day, "day": LABEL[day], "who": who,
                         "lines": [board_line(day, text, names) for text, names in lines]}
                        for day, who, lines in rows]
    state["weekend_overflow"] = overflow
    audit = {"source_updated_at": context["freshness"]["source_updated_at"], "facts": []}
    for item in context["items"]:
        location = placements.get(item["ref"])
        audit["facts"].append({"ref": item["ref"], "decision": "displayed" if location else "omitted",
                               "location": location or "", "reason": "Test placement"})
    return state, audit


def texts(rows):
    return [(row["who"], [(entry["text"], entry["kind"], entry["label"]) for entry in row["lines"]])
            for row in rows]


def refs(context, **match):
    return [item["ref"] for item in context["items"]
            if all(item.get(key) == value for key, value in match.items())]


def test_one_active_party_appears_on_its_date_in_both_renderers(tmp_path):
    _, context = export(tmp_path, {"a": [fact(1)]})
    [party] = context["items"]
    assert party["title"] == "Fødselsdagsfest" and party["message_thread"] == 1 and "cancelled" not in party
    state, audit = compose(context, [(SUNDAY, "ChildA", [("Fødselsdagsfest", [party["ref"]])])],
                           {party["ref"]: "weekend[0]"})
    validate_composition(context, state, audit)
    assert "Fødselsdagsfest" in render_html(state)
    render_png(state)
    state["weekend"] = []
    audit["facts"][0].update(decision="omitted", location="")
    with pytest.raises(ValueError, match="confirmed weekend activity must be displayed"):
        validate_composition(context, state, audit)


def test_a_named_birthday_reads_as_whose_party_it_is_everywhere(tmp_path):
    from skoleklar.dashboard_rules import weekend_selection
    from skoleklar.delivery_manifest import _phone_title

    items, context = export(tmp_path, {"a": [fact(1, celebrant="Clara")]})
    assert items[0]["title"] == "Claras fødselsdag" and items[0]["celebrant"] == "Clara"
    assert _phone_title(items[0]) == "ChildA · Claras fødselsdag"
    brief = build_brief_range(items, start_date=date(2026, 10, 24), end_date=date(2026, 11, 2), compact=True)
    assert brief["items"][0]["title"] == "Claras fødselsdag" and brief["items"][0]["celebrant"] == "Clara"
    [party] = context["items"]
    # The name is in the title and, as the fact model has it, in `celebrant`.
    assert party["title"] == "Claras fødselsdag" and party["celebrant"] == "Clara"
    rows, overflow, _ = weekend_selection(context)
    assert texts(rows) == [("ChildA", [("Claras fødselsdag", "party", "")])] and overflow == 0
    state, audit = compose(context, [(SUNDAY, "ChildA", [("Claras fødselsdag", [party["ref"]])])],
                           {party["ref"]: "weekend[0]"})
    validate_composition(context, state, audit)
    assert "Claras fødselsdag" in render_html(state)
    render_png(state)


def test_a_named_birthday_that_is_cancelled_still_says_so(tmp_path):
    from skoleklar.dashboard_rules import weekend_selection

    _, context = export(tmp_path, {"a": [fact(1, celebrant="Mads", cancelled=True)]})
    [party] = context["items"]
    # The title is the party's; `cancelled` says it is off, and the board labels it.
    assert party["title"] == "Mads' fødselsdag" and party["cancelled"] is True
    rows, _, _ = weekend_selection(context)
    assert texts(rows) == [("ChildA", [("Mads' fødselsdag", "party", "Aflyst")])]
    state, audit = compose(context, [(SUNDAY, "ChildA", [("Mads' fødselsdag", [party["ref"]])])],
                           {party["ref"]: "weekend[0]"})
    validate_composition(context, state, audit)
    assert "<b>Aflyst</b> Mads&#x27; fødselsdag" in render_html(state)


def test_cancellation_survives_the_brief_and_context_and_is_never_a_plan(tmp_path):
    items, context = export(tmp_path, {"a": [fact(1, cancelled=True)]})
    assert items[0]["state"] == "cancelled"
    brief = build_brief_range(items, start_date=date(2026, 10, 24), end_date=date(2026, 11, 2), compact=True)
    assert brief["items"][0]["state"] == "cancelled"
    [party] = context["items"]
    assert party["cancelled"] is True and party["title"] == "Fødselsdagsfest"
    validate_composition(context, *compose(context, [], {}))
    state, audit = compose(context, [(SUNDAY, "ChildA", [("Fødselsdagsfest", [party["ref"]])])],
                           {party["ref"]: "weekend[0]"})
    validate_composition(context, state, audit)
    assert "<b>Aflyst</b> Fødselsdagsfest" in render_html(state)
    render_png(state)


def test_a_later_cancellation_in_the_same_thread_cancels_the_party(tmp_path):
    from skoleklar.dashboard_rules import weekend_selection

    _, context = export(tmp_path, {"a": [fact(1), fact(2, event_kind="cancellation")]})
    party = refs(context, activity_kind="birthday_party")[0]
    with pytest.raises(ValueError, match="cancelled activity must not appear as a plan"):
        validate_composition(context, *compose(context, [(SUNDAY, "ChildA", [("Fødselsdagsfest", [party])])],
                                               {party: "weekend[0]"}))
    validate_composition(context, *compose(context, [(SUNDAY, "ChildA", [("Fødselsdagsfest", [party + "!"])])],
                                           {party: "weekend[0]"}))
    validate_composition(context, *compose(context, [], {}))
    rows, _, _ = weekend_selection(context)
    assert texts(rows)[0] == ("ChildA", [("Fødselsdagsfest", "party", "Aflyst")])


def test_three_facts_for_one_trip_make_one_row_and_three_cannot_switch_validation_off(tmp_path):
    _, context = export(tmp_path, {letter: [fact(number, kind="class_trip")]
                                   for number, letter in enumerate("abc", 1)})
    trips = refs(context, activity_kind="class_trip")
    assert len(trips) == 3
    validate_composition(context, *compose(context, [(SUNDAY, "ChildA", [("Klassetur", trips)])],
                                           {ref: "weekend[0]" for ref in trips}))
    with pytest.raises(ValueError, match="confirmed weekend activity must be displayed"):
        validate_composition(context, *compose(context, [], {}))


def test_three_distinct_activities_fill_both_rows_and_count_the_rest(tmp_path):
    _, context = export(tmp_path, {"a": [fact(1, day=SATURDAY)], "b": [fact(2)],
                                   "c": [fact(3, child="ChildB", kind="social_activity")]})
    first, second = refs(context, activity_kind="birthday_party")
    third = refs(context, activity_kind="social_activity")[0]
    rows = [(SATURDAY, "ChildA", [("Fødselsdagsfest", [first])]), (SUNDAY, "ChildA", [("Fødselsdagsfest", [second])])]
    placements = {first: "weekend[0]", second: "weekend[1]", third: "weekend_overflow"}
    state, audit = compose(context, rows, placements, overflow=1)
    validate_composition(context, state, audit)
    # Without the weekend forecast the parents' board counts the third on the last row.
    assert "Fødselsdagsfest (+1 mere)" in render_html(state)
    render_png(state)
    cover = {**state, "weather": weather_state()["weather"]}
    assert "Fødselsdagsfest (+1 mere)" in render_html(cover)
    render_png(cover)

    with pytest.raises(ValueError, match="weekend overflow must count"):
        validate_composition(context, *compose(context, rows, placements))
    with pytest.raises(ValueError, match="confirmed weekend activity must be displayed"):
        validate_composition(context, *compose(context, rows, {first: "weekend[0]", second: "weekend[1]"},
                                               overflow=1))
    with pytest.raises(ValueError, match="both rows"):
        validate_composition(context, *compose(context, rows[:1], {first: "weekend[0]",
                                                                   second: "weekend_overflow",
                                                                   third: "weekend_overflow"}, overflow=2))


def test_two_parties_on_one_day_stay_distinct(tmp_path):
    _, context = export(tmp_path, {"a": [fact(1)], "b": [fact(2)]})
    first, second = refs(context, activity_kind="birthday_party")
    assert {item["message_thread"] for item in context["items"]} == {1, 2}
    merged = compose(context, [(SUNDAY, "ChildA", [("Fødselsdagsfest", [first, second])])],
                     {first: "weekend[0]", second: "weekend[0]"})
    with pytest.raises(ValueError, match="distinct weekend activities must not share a row"):
        validate_composition(context, *merged)
    validate_composition(context, *compose(
        context, [(SUNDAY, "ChildA", [("Fødselsdagsfest", [first])]),
                  (SUNDAY, "ChildA", [("Fødselsdagsfest", [second])])],
        {first: "weekend[0]", second: "weekend[1]"}))


def test_a_party_and_its_bring_item_share_one_row(tmp_path):
    """The weekend of 4 November 2026: one party on Saturday, two on Sunday from two
    threads, and a required bring-item in the same message as one Sunday party."""
    from skoleklar.dashboard_rules import weekend_selection

    bring = {**fact(4, event_kind="bring"), "bring_items": ["udetøj"], "requirement": "required"}
    _, context = export(tmp_path, {"a": [fact(1, child="ChildB", day=SATURDAY)],
                                   "b": [fact(2), bring], "c": [fact(3)]})
    saturday = refs(context, activity_kind="birthday_party", child="ChildB")[0]
    with_item, other = (refs(context, activity_kind="birthday_party", child="ChildA", message_thread=n)[0]
                        for n in (2, 3))
    [item] = refs(context, type="bring")
    # The message's bring-item carries its things, so the board needs no words to find them.
    assert next(fact for fact in context["items"] if fact["ref"] == item)["bring_items"] == ["udetøj"]
    rows, overflow, _ = weekend_selection(context)
    assert texts(rows) == [("ChildB", [("Fødselsdagsfest", "party", "")]),
                           ("ChildA", [("Fødselsdagsfest", "party", ""), ("udetøj", "bring", "Medbring")])]
    assert overflow == 1
    rows = [(SATURDAY, "ChildB", [("Fødselsdagsfest", [saturday])]),
            (SUNDAY, "ChildA", [("Fødselsdagsfest", [with_item]), ("udetøj", [item])])]
    placements = {saturday: "weekend[0]", with_item: "weekend[1]", item: "weekend[1]", other: "weekend_overflow"}
    validate_composition(context, *compose(context, rows, placements, overflow=1))
    # The bring-item may not be dropped while its party is shown.
    without = [rows[0], (SUNDAY, "ChildA", [("Fødselsdagsfest", [with_item])])]
    with pytest.raises(ValueError, match="private event row must include its retained bring-item"):
        validate_composition(context, *compose(context, without, {**placements, item: None}, overflow=1))


def test_birthday_notice_never_takes_a_row_and_an_invitation_gets_it():
    from tests_python.test_dashboard_events import activity_row

    context, state, audit = weekend_fixture()
    notice, trip = context["items"]
    parties = [{"ref": f"fact-{n + 2}", "type": "event", "child": "ChildA", "effective_date": SUNDAY,
                "title": "Fødselsdagsfest", "private_message_fact": True, "activity_kind": "birthday_party",
                "message_thread": n} for n in (1, 2)]
    context["items"] = [notice, trip, *parties]
    state["weekend"] = [activity_row(context, ["fact-2"]), activity_row(context, ["fact-1"])]
    state["weekend_overflow"] = 2
    audit["facts"] = [
        {"ref": "fact-1", "decision": "displayed", "location": "weekend[1]", "reason": "Reminder"},
        {"ref": "fact-2", "decision": "displayed", "location": "weekend[0]", "reason": "Trip"},
        *({"ref": p["ref"], "decision": "merged", "location": "weekend_overflow", "reason": "No room"}
          for p in parties)]
    with pytest.raises(ValueError, match="birthday notice is not a family activity"):
        validate_composition(context, state, audit)
    state["weekend"][1] = activity_row(context, ["fact-3"])
    state["weekend_overflow"] = 1
    audit["facts"][0].update(decision="omitted", location="")
    audit["facts"][2].update(decision="displayed", location="weekend[1]")
    validate_composition(context, state, audit)


def test_what_a_derived_fact_is_reaches_the_board_context(tmp_path):
    """The fact model's fields travel from the store to the context; a fact made under an
    older contract is counted, so the board can say so and the scan re-derives it."""
    from skoleklar import fact as fact_model
    from skolekit.records import SourceRecord
    from skoleklar.dashboard_context import collapse_duplicates

    path = tmp_path / "facts.db"
    with SnapshotStore(path) as store:
        run = store.begin_run()
        notice = SourceRecord(student_id=1, child="ChildA", source_type="frontpage", source_key="notice:1",
                              source_url="https://example.invalid/notice/1", title="Tur", body_text="Skovtur.",
                              source_data={}, content_data={}, raw_payload={})
        store.store_checks(run, [SourceCheck(student_id=index, child=child, source_type="frontpage",
                                             records=[notice] if child == "ChildA" else [], complete=True)
                                 for index, child in enumerate(("ChildA", "ChildB"), 1)])
        [candidate] = store.derivation_candidates(run)
        base = {"stream": "calendar", "type": "event", "audience": "family", "child": "ChildA",
                "summary": "Klassen tager i skoven.", "effective_date": "2026-10-27", "end_date": None,
                "all_day": True, "timing_rule": "explicit", "actionability": "informational",
                "urgency": "routine", "urgency_reason": "", "confidence": 0.9, "needs_review": False,
                "review_reason": "", "evidence": [{"quote": "Skovtur", "source_day": None, "subject": None}]}
        derivation = store.begin_derivation(harvest_run_id=run, cache_key="c", provider="test", model="m",
                                            prompt_version="derive-v11", taxonomy_version="t")
        store.complete_derivation(derivation, items=[
            fact_model.normalize(base | {"stable_key": "trip", "dedupe_hint": "trip", "title": "Skovtur",
                                         "category": "trip"}),
            base | {"stable_key": "older", "dedupe_hint": "older", "title": "Skovdag"},
        ], sources=candidate["sources"], response_id="r", usage={})
    with SnapshotStore(path, read_only=True) as store:
        context = export_context(store, now=datetime.fromisoformat("2026-10-26T19:30:00+01:00"))
    facts = {fact["title"]: fact for fact in context["items"]}
    # What is empty for a fact is left out of the model's context.
    assert facts["Skovtur"]["category"] == "trip"
    assert not {"moves_time", "recurring", "bring_items", "school_subject"} & set(facts["Skovtur"])
    assert not set(fact_model.names()) & set(facts["Skovdag"])
    assert context["contract"] == {"current": fact_model.CONTRACT, "stale": 1}

    # The same fact twice is one fact; what it is joins as consolidation joins it.
    first = {"ref": "fact-1", "type": "event", "child": "ChildA", "title": "Skovtur",
             "effective_date": "2026-10-27", "category": "trip"}
    [kept] = collapse_duplicates([dict(first), {**first, "ref": "fact-2"}])
    assert kept["repeats"] == 2 and kept["category"] == "trip"
    [kept] = collapse_duplicates([dict(first), {**first, "ref": "fact-2", "category": "party"}])
    assert "category" not in kept
