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
    """A controlled composition: `rows` of (date, text); `placements` maps each fact ref to a location."""
    _, template, _ = weekend_fixture()
    state = restamp(template, context)
    state["weekend"] = [{"day": LABEL[day], "text": text} for day, text in rows]
    state["weekend_overflow"] = overflow
    audit = {"source_updated_at": context["freshness"]["source_updated_at"], "facts": []}
    for item in context["items"]:
        location = placements.get(item["ref"])
        audit["facts"].append({"ref": item["ref"], "decision": "displayed" if location else "omitted",
                               "location": location or "", "reason": "Test placement"})
    return state, audit


def refs(context, **match):
    return [item["ref"] for item in context["items"]
            if all(item.get(key) == value for key, value in match.items())]


def test_one_active_party_appears_on_its_date_in_both_renderers(tmp_path):
    _, context = export(tmp_path, {"a": [fact(1)]})
    [party] = context["items"]
    assert party["title"] == "Fødselsdagsfest" and party["message_thread"] == 1 and "cancelled" not in party
    state, audit = compose(context, [(SUNDAY, "ChildA: Fødselsdagsfest")],
                           {party["ref"]: "weekend[0].text"})
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
    # The context carries the name in the title only: no new field reaches the model or MCP.
    assert party["title"] == "Claras fødselsdag" and "celebrant" not in party
    rows, overflow, _ = weekend_selection(context)
    assert [row["text"] for row in rows] == ["ChildA: Claras fødselsdag"] and overflow == 0
    state, audit = compose(context, [(SUNDAY, "ChildA: Claras fødselsdag")], {party["ref"]: "weekend[0].text"})
    validate_composition(context, state, audit)
    assert "Claras fødselsdag" in render_html(state)
    render_png(state)


def test_a_named_birthday_that_is_cancelled_still_says_so(tmp_path):
    _, context = export(tmp_path, {"a": [fact(1, celebrant="Mads", cancelled=True)]})
    [party] = context["items"]
    assert party["title"] == "Mads' fødselsdag aflyst"
    state, audit = compose(context, [(SUNDAY, "ChildA: Mads' fødselsdag aflyst")],
                           {party["ref"]: "weekend[0].text"})
    validate_composition(context, state, audit)


@pytest.mark.parametrize("celebrant, cancelled", [("Aaaaaaaaaaaaaaaaaaaaaaaa", False),
                                                   ("Ella-Rose", True)])
def test_a_long_name_gives_way_before_the_activity_or_its_cancellation(celebrant, cancelled):
    from skoleklar.dashboard_events import activity_title
    from skoleklar.dashboard_rules import WEEKEND_TEXT_LIMIT, _activity_text

    child = "Bbbbbbbbbbbbbbbbbbbbbbbb"
    group = {"child": child, "kind": "birthday_party", "label": activity_title("birthday_party", celebrant),
             "cancelled": cancelled, "bring": []}
    text = _activity_text(group, [child])
    assert len(text) <= WEEKEND_TEXT_LIMIT and "…" not in text
    assert "fødselsdag" in text.casefold() and ("aflyst" in text) == cancelled
    assert text == f"{child}: Fødselsdagsfest" + (" aflyst" if cancelled else "")


def test_cancellation_survives_the_brief_and_context_and_is_never_a_plan(tmp_path):
    items, context = export(tmp_path, {"a": [fact(1, cancelled=True)]})
    assert items[0]["state"] == "cancelled"
    brief = build_brief_range(items, start_date=date(2026, 10, 24), end_date=date(2026, 11, 2), compact=True)
    assert brief["items"][0]["state"] == "cancelled"
    [party] = context["items"]
    assert party["cancelled"] is True and party["title"] == "Fødselsdagsfest aflyst"

    as_plan = compose(context, [(SUNDAY, "ChildA: Fødselsdagsfest")], {party["ref"]: "weekend[0].text"})
    with pytest.raises(ValueError, match="cancelled activity must not appear as a plan"):
        validate_composition(context, *as_plan)
    validate_composition(context, *compose(context, [], {}))
    state, audit = compose(context, [(SUNDAY, "ChildA: Fødselsdagsfest aflyst")],
                           {party["ref"]: "weekend[0].text"})
    validate_composition(context, state, audit)
    assert "Fødselsdagsfest aflyst" in render_html(state)
    render_png(state)


def test_a_later_cancellation_in_the_same_thread_cancels_the_party(tmp_path):
    _, context = export(tmp_path, {"a": [fact(1), fact(2, event_kind="cancellation")]})
    party = refs(context, activity_kind="birthday_party")[0]
    with pytest.raises(ValueError, match="cancelled activity must not appear as a plan"):
        validate_composition(context, *compose(context, [(SUNDAY, "ChildA: Fødselsdagsfest")],
                                               {party: "weekend[0].text"}))
    validate_composition(context, *compose(context, [], {}))


def test_three_facts_for_one_trip_make_one_row_and_three_cannot_switch_validation_off(tmp_path):
    _, context = export(tmp_path, {letter: [fact(number, kind="class_trip")]
                                   for number, letter in enumerate("abc", 1)})
    trips = refs(context, activity_kind="class_trip")
    assert len(trips) == 3
    validate_composition(context, *compose(context, [(SUNDAY, "ChildA: Klassetur")],
                                           {ref: "weekend[0].text" for ref in trips}))
    with pytest.raises(ValueError, match="confirmed weekend activity must be displayed"):
        validate_composition(context, *compose(context, [], {}))


def test_three_distinct_activities_fill_both_rows_and_count_the_rest(tmp_path):
    _, context = export(tmp_path, {"a": [fact(1, day=SATURDAY)], "b": [fact(2)],
                                   "c": [fact(3, child="ChildB", kind="social_activity")]})
    first, second = refs(context, activity_kind="birthday_party")
    third = refs(context, activity_kind="social_activity")[0]
    rows = [(SATURDAY, "ChildA: Fødselsdagsfest"), (SUNDAY, "ChildA: Fødselsdagsfest")]
    placements = {first: "weekend[0].text", second: "weekend[1].text", third: "weekend_overflow"}
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
        validate_composition(context, *compose(context, rows, {first: "weekend[0].text",
                                                               second: "weekend[1].text"}, overflow=1))
    with pytest.raises(ValueError, match="both rows"):
        validate_composition(context, *compose(context, rows[:1], {first: "weekend[0].text",
                                                                   second: "weekend_overflow",
                                                                   third: "weekend_overflow"}, overflow=2))


def test_two_parties_on_one_day_stay_distinct(tmp_path):
    _, context = export(tmp_path, {"a": [fact(1)], "b": [fact(2)]})
    first, second = refs(context, activity_kind="birthday_party")
    assert {item["message_thread"] for item in context["items"]} == {1, 2}
    merged = compose(context, [(SUNDAY, "ChildA: Fødselsdagsfest")],
                     {first: "weekend[0].text", second: "weekend[0].text"})
    with pytest.raises(ValueError, match="distinct weekend activities must not share a row"):
        validate_composition(context, *merged)
    validate_composition(context, *compose(
        context, [(SUNDAY, "ChildA: Fødselsdagsfest"), (SUNDAY, "ChildA: Fødselsdagsfest")],
        {first: "weekend[0].text", second: "weekend[1].text"}))


def test_a_party_and_its_bring_item_share_one_row_without_besked(tmp_path):
    """The weekend of 4 November 2026: one party on Saturday, two on Sunday from two
    threads, and a required bring-item in the same message as one Sunday party."""
    bring = {**fact(4, event_kind="bring"), "bring_items": ["udetøj"], "requirement": "required"}
    _, context = export(tmp_path, {"a": [fact(1, child="ChildB", day=SATURDAY)],
                                   "b": [fact(2), bring], "c": [fact(3)]})
    saturday = refs(context, activity_kind="birthday_party", child="ChildB")[0]
    with_item, other = (refs(context, activity_kind="birthday_party", child="ChildA", message_thread=n)[0]
                        for n in (2, 3))
    [item] = refs(context, type="bring")
    rows = [(SATURDAY, "ChildB: Fødselsdagsfest"), (SUNDAY, "ChildA: Fødselsdagsfest; Medbring: udetøj")]
    placements = {saturday: "weekend[0].text", with_item: "weekend[1].text", item: "weekend[1].text",
                  other: "weekend_overflow"}
    # The row the prompt asks for: the event, with its bring-item and no `Besked:`.
    validate_composition(context, *compose(context, rows, placements, overflow=1))
    # The bring-item may not be dropped while its party is shown.
    with pytest.raises(ValueError, match="private event row must include its retained bring-item"):
        validate_composition(context, *compose(context, rows, {**placements, item: None}, overflow=1))
    # A party row still reads as an event.
    besked = [rows[0], (SUNDAY, "ChildA: Besked: Fødselsdagsfest; udetøj")]
    with pytest.raises(ValueError, match="weekend activity must read as an event"):
        validate_composition(context, *compose(context, besked, placements, overflow=1))


def test_a_message_bring_item_on_a_row_without_an_activity_still_says_besked():
    context, state, audit = weekend_fixture()
    context["items"].append({"ref": "bring", "type": "bring", "child": "ChildA", "effective_date": SUNDAY,
                             "title": "Bring udetøj", "private_message_fact": True,
                             "actionability": "required"})
    state["weekend"] = [{"day": "Søn. 25. okt.", "text": "ChildA: Klassetur"},
                        {"day": "Søn. 25. okt.", "text": "ChildA: Medbring: udetøj"}]
    audit["facts"] = [{"ref": "birthday", "decision": "omitted", "location": "", "reason": "Birth date only"},
                      {"ref": "trip", "decision": "displayed", "location": "weekend[0].text", "reason": "Trip"},
                      {"ref": "bring", "decision": "displayed", "location": "weekend[1].text", "reason": "Item"}]
    with pytest.raises(ValueError, match="private message fact must show message provenance"):
        validate_composition(context, state, audit)
    state["weekend"][1]["text"] = "ChildA: Besked: Medbring: udetøj"
    validate_composition(context, state, audit)


def test_birthday_notice_never_takes_a_row_and_an_invitation_gets_it():
    context, state, audit = weekend_fixture()
    notice, trip = context["items"]
    parties = [{"ref": f"party{n}", "type": "event", "child": "ChildA", "effective_date": SUNDAY,
                "title": "Fødselsdagsfest", "private_message_fact": True, "activity_kind": "birthday_party",
                "message_thread": n} for n in (1, 2)]
    context["items"] = [notice, trip, *parties]
    state["weekend"] = [{"day": "Søn. 25. okt.", "text": "ChildA: Klassetur"},
                        {"day": "Søn. 25. okt.", "text": "ChildA · Alex fødselsdag"}]
    state["weekend_overflow"] = 2
    audit["facts"] = [
        {"ref": "birthday", "decision": "displayed", "location": "weekend[1].text", "reason": "Reminder"},
        {"ref": "trip", "decision": "displayed", "location": "weekend[0].text", "reason": "Trip"},
        *({"ref": p["ref"], "decision": "merged", "location": "weekend_overflow", "reason": "No room"}
          for p in parties)]
    with pytest.raises(ValueError, match="birthday notice is not a family activity"):
        validate_composition(context, state, audit)
    state["weekend"][1]["text"] = "ChildA: Fødselsdagsfest"
    state["weekend_overflow"] = 1
    audit["facts"][0].update(decision="omitted", location="")
    audit["facts"][2].update(decision="displayed", location="weekend[1].text")
    validate_composition(context, state, audit)


def test_what_a_derived_fact_is_reaches_the_board_context(tmp_path):
    """derive-v10's fields travel from the store to the context, only when they are set."""
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
                                            prompt_version="derive-v10", taxonomy_version="t")
        store.complete_derivation(derivation, items=[
            base | {"stable_key": "trip", "dedupe_hint": "trip", "title": "Skovtur", "category": "trip",
                    "moves_time": None, "recurring": False},
            base | {"stable_key": "older", "dedupe_hint": "older", "title": "Skovdag"},
        ], sources=candidate["sources"], response_id="r", usage={})
    with SnapshotStore(path, read_only=True) as store:
        context = export_context(store, now=datetime.fromisoformat("2026-10-26T19:30:00+01:00"))
    facts = {fact["title"]: fact for fact in context["items"]}
    assert (facts["Skovtur"]["category"], facts["Skovtur"]["recurring"]) == ("trip", False)
    assert "moves_time" not in facts["Skovtur"]
    assert not {"category", "moves_time", "recurring"} & set(facts["Skovdag"])

    # The same fact derived before and after v10 is still one fact, and says what it is.
    older = {"ref": "fact-1", "type": "event", "child": "ChildA", "title": "Skovtur", "effective_date": "2026-10-27"}
    newer = {**older, "ref": "fact-2", "category": "trip", "recurring": False}
    [kept] = collapse_duplicates([dict(older), newer])
    assert kept["repeats"] == 2 and kept["category"] == "trip" and kept["recurring"] is False


def test_a_weekend_rows_kind_not_its_words_shows_the_activity(tmp_path):
    _, context = export(tmp_path, {"a": [fact(1, celebrant="Clara")]})
    [party] = context["items"]
    # The row's kind is the party, so its words need not say 'fødselsdag'.
    state, audit = compose(context, [(SUNDAY, "ChildA: Claras fest")], {party["ref"]: "weekend[0].text"})
    state["weekend"][0]["kinds"] = ["party"]
    validate_composition(context, state, audit)
    state["weekend"][0]["kinds"] = ["event"]
    with pytest.raises(ValueError, match="weekend activity must keep its kind"):
        validate_composition(context, state, audit)
    # A row without kinds is held to its words, as before.
    state["weekend"][0].pop("kinds")
    with pytest.raises(ValueError, match="birthday party must retain its activity kind"):
        validate_composition(context, state, audit)
