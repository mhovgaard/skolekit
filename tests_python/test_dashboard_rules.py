import json
import logging
from datetime import date, timedelta

import pytest

from skoleklar.runtime import scan as scan_service
from skoleklar.dashboard_composition import CompositionFailed, compose, validation_reason
from skoleklar.dashboard_build import build_state, fallback_composition
from skoleklar.dashboard_rules import (
    fact_text,
    repair_markers,
    weekend_selection,
)
from skoleklar.dashboard_validation import RuleViolation, validate_composition
from tests_python.test_dashboard_timeline import composed_week, context_at, local, party


def fact(ref, child, day, kind="event", **values):
    base = {"ref": ref, "child": child, "effective_date": day, "end_date": None, "type": kind,
            "title": values.pop("title", "Punkt"), "summary": "", "actionability": "informational",
            "urgency": "routine", "confidence": 0.9, "needs_review": False,
            "private_message_fact": False, "grouped": False}
    return {**base, **values}


def context(now, items):
    return {**context_at(local(now), updated=now), "items": items}


def bring(ref, child, day, thread, items="udetøj", actionability="required"):
    return fact(ref, child, day, "bring", title=f"Medbring {items}", private_message_fact=True,
                message_thread=thread, actionability=actionability)


def audit_for(ctx, placements=None):
    placements = placements or {}
    return {"source_updated_at": ctx["freshness"]["source_updated_at"],
            "facts": [{"ref": item["ref"], **placements.get(item["ref"], {
                "decision": "omitted", "location": "", "reason": "Not needed"})}
                      for item in ctx["items"]]}


def test_weekend_rows_keep_a_bring_item_with_its_own_party_and_count_the_rest():
    # Thursday evening: a Saturday party, and two Sunday parties of which one has a bring-item.
    ctx = context("2026-11-05T19:35:00", [
        party("fact-1", "ChildB", "2026-11-07", 3),
        bring("fact-2", "ChildA", "2026-11-08", 4),
        party("fact-3", "ChildA", "2026-11-08", 5),
        party("fact-4", "ChildA", "2026-11-08", 4),
        fact("fact-5", "ChildA", "2026-11-14", title="Brynhilde har fødselsdag"),
    ])
    rows, overflow, decisions = weekend_selection(ctx)
    # Each part's kind travels with the row: the activity, then what to bring.
    assert rows == [{"day": "Lør. 7. nov.", "text": "ChildB: Fødselsdagsfest", "kinds": ["party"]},
                    {"day": "Søn. 8. nov.", "text": "ChildA: Fødselsdagsfest; Medbring: udetøj",
                     "kinds": ["party", "bring"]}]
    assert overflow == 1
    assert decisions == {
        "fact-1": ("displayed", "weekend[0]", "Weekend activity"),
        "fact-2": ("merged", "weekend[1]", "Bring item for activity"),
        "fact-3": ("merged", "weekend_overflow", "Weekend activity overflow"),
        "fact-4": ("displayed", "weekend[1]", "Weekend activity"),
    }


def test_cancelled_parties_show_as_cancelled_and_other_facts_fill_free_rows():
    ctx = context("2026-11-05T19:35:00", [
        party("fact-1", "ChildA", "2026-11-07", 1) | {"cancelled": True, "title": "Fødselsdagsfest aflyst"},
        bring("fact-2", "ChildA", "2026-11-07", 1),
        fact("fact-3", "household", "2026-11-08", title="Loppemarked på skolen"),
        fact("fact-4", "ChildB", "2026-11-08", title="Måske tur", needs_review=True),
        fact("fact-5", "ChildB", "2026-11-07", title="Lise har fødselsdag"),
    ])
    rows, overflow, decisions = weekend_selection(ctx)
    assert rows == [{"day": "Lør. 7. nov.", "text": "ChildA: Fødselsdagsfest aflyst", "kinds": ["party"]},
                    {"day": "Søn. 8. nov.", "text": "Begge: Loppemarked på skolen", "kinds": ["event"]}]
    assert overflow == 0
    assert decisions["fact-2"] == ("omitted", "", "Cancelled activity")
    assert decisions["fact-4"] == ("omitted", "", "Needs review")
    assert decisions["fact-5"] == ("omitted", "", "Classmate birth date only")


def test_fact_text_adds_the_markers_each_fact_requires():
    assert fact_text(bring("f", "A", "2026-11-08", 1, "udetøj og gummistøvler")) == \
        "Besked: Medbring: udetøj og gummistøvler"
    assert fact_text(fact("f", "A", "d", "bring", title="Hinkesten", actionability="recommended")) == \
        "Valgfrit: Hinkesten"
    assert fact_text(fact("f", "A", "d", "homework", title="Ugehistorie")) == "Lektie: Ugehistorie"
    assert fact_text(fact("f", "A", "d", "substitute", title="Thorbjørn Zeuthen Gørtz er vikar i KULTUR · 00")) == \
        "Vikar: Thorbjørn G. · KULTUR 00"
    assert fact_text(fact("f", "A", "d", private_message_fact=True, title="Skolearrangement")) == \
        "Besked: Skolearrangement"


def test_marker_repair_fixes_exact_lines_and_leaves_the_rest():
    ctx = context("2026-11-05T08:00:00", [
        fact("fact-1", "ChildA", "2026-11-05", private_message_fact=True, title="Klassearrangement",
             activity_kind="class_event", message_thread=1),
        fact("fact-2", "ChildA", "2026-11-05", "bring", title="Hinkesten", actionability="recommended"),
        fact("fact-3", "ChildB", "2026-11-05", "bring", title="Idrætstøj", actionability="required"),
        fact("fact-4", "ChildB", "2026-11-05", "substitute", title="Alex Winther er vikar i MAT · 02"),
        fact("fact-5", "ChildB", "2026-11-05", private_message_fact=True, title="Skolearrangement"),
    ])
    state, _ = composed_week(ctx)
    state["hero"]["children"][0]["lines"] = ["Klassearrangement", "Medbring: hinkesten"]
    state["hero"]["children"][1]["lines"] = ["idrætstøj", "Vikar i matematik"]
    audit = audit_for(ctx, {
        "fact-1": {"decision": "displayed", "location": "hero.children[0].lines[0]", "reason": "x"},
        "fact-2": {"decision": "displayed", "location": "hero.children[0].lines[1]", "reason": "x"},
        "fact-3": {"decision": "displayed", "location": "hero.children[1].lines[0]", "reason": "x"},
        "fact-4": {"decision": "displayed", "location": "hero.children[1].lines[1]", "reason": "x"},
        "fact-5": {"decision": "displayed", "location": "hero.children[1]", "reason": "x"},
    })
    repaired = repair_markers(state, audit, ctx)
    assert state["hero"]["children"][0]["lines"] == ["Besked: Klassearrangement", "Valgfrit: hinkesten"]
    assert state["hero"]["children"][1]["lines"] == ["Medbring: idrætstøj", "Vikar: Alex W. · MAT 02"]
    assert sorted(repaired) == ["hero.children[0].lines[0]", "hero.children[0].lines[1]",
                                "hero.children[1].lines[0]", "hero.children[1].lines[1]"]


def test_compose_lets_code_write_weekend_and_markers_from_the_models_slots():
    from tests_python.test_display_services import _slots
    ctx = context("2026-11-05T19:35:00", [
        fact("fact-1", "ChildA", "2026-11-06", private_message_fact=True, title="Klassearrangement",
             activity_kind="class_event", message_thread=1),
        bring("fact-2", "ChildA", "2026-11-08", 4),
        party("fact-3", "ChildA", "2026-11-08", 5),
        party("fact-4", "ChildA", "2026-11-08", 4),
        party("fact-5", "ChildB", "2026-11-07", 3),
    ])
    line = [{"refs": ["fact-1"], "priority": "event", "text": "Klassearrangement"}]
    answer = _slots(ctx, {("2026-11-06", "ChildA"): line},
                    [{"ref": ref, "reason": "weekend", "same_as": None} for ref in ("fact-2", "fact-3", "fact-4", "fact-5")])

    class Client:
        calls = 0

        def generate(self, document, **_):
            self.calls += 1
            return {"output": json.loads(json.dumps(answer))}

    client = Client()
    composed, selection = compose(ctx, client)
    assert client.calls == 1
    assert composed["hero"]["children"][0]["lines"] == ["Besked: Klassearrangement"]
    assert composed["weekend"][1]["text"] == "ChildA: Fødselsdagsfest; Medbring: udetøj"
    assert composed["weekend_overflow"] == 1
    validate_composition(ctx, composed, selection)


def test_builder_checks_each_omission_tag_against_the_facts():
    from tests_python.test_display_services import _slots
    ctx = context("2026-11-02T08:00:00", [
        fact("fact-1", "ChildA", "2026-11-02", "homework", title="Læs side 12", actionability="required"),
        fact("fact-2", "ChildA", "2026-11-03", "event", title="Teatertur"),
        fact("fact-3", "ChildB", "2026-11-03", "context", title="Ugens tema er efterår"),
        fact("fact-4", "ChildB", "2026-11-04", "event", title="5. klasse på museum"),
        fact("fact-5", "ChildB", "2026-11-04", "event", title="Teatertur"),
        fact("fact-6", "ChildA", "2026-11-05", "substitute", title="Alex Winther er vikar i MAT · 02"),
        fact("fact-7", "ChildA", "2026-11-20", "event", title="Juleklip"),
    ])
    answer = _slots(ctx, {("2026-11-03", "ChildA"): [
        # A wrong-date reference is dropped from the line; the right one stays.
        {"refs": ["fact-2", "fact-1"], "priority": "event", "text": "Teatertur i dag"}]}, [
        {"ref": "fact-1", "reason": "capacity", "same_as": None},       # Monday is empty: shown
        {"ref": "fact-3", "reason": "not_relevant", "same_as": None},   # informational: accepted
        {"ref": "fact-4", "reason": "other_class", "same_as": None},    # judgement: accepted
        {"ref": "fact-5", "reason": "duplicate", "same_as": "fact-9"},  # unknown fact: shown
        {"ref": "fact-6", "reason": "not_relevant", "same_as": None},   # a change: shown
        {"ref": "fact-7", "reason": "uncertain", "same_as": None},      # outside the week anyway
    ])
    state, audit = build_state(ctx, answer)
    reasons = {item["ref"]: (item["decision"], item["reason"]) for item in audit["facts"]}
    assert state["hero"]["children"][0]["lines"] == ["Læs side 12"]
    assert state["weekdays"][1]["children"][0]["lines"] == ["Teatertur tir."]
    assert state["weekdays"][2]["children"][1]["lines"] == ["Teatertur"]
    assert state["weekdays"][3]["children"][0]["lines"] == ["Vikar: Alex W. · MAT 02"]
    assert reasons["fact-3"] == ("omitted", "Not relevant")
    assert reasons["fact-4"] == ("omitted", "Other class")
    assert reasons["fact-7"] == ("omitted", "Outside displayed week")
    assert state["weekdays"][0]["children"][0]["heading"] == "Lektier"


def test_a_fact_is_only_a_duplicate_of_a_line_for_the_same_child():
    from tests_python.test_display_services import _slots
    ctx = context("2026-11-02T08:00:00", [
        fact("fact-1", "ChildA", "2026-11-03", title="Skole-hjem-samtale kl. 15"),
        fact("fact-2", "ChildB", "2026-11-03", title="Skole-hjem-samtaler for 0. klasse"),
        fact("fact-3", "ChildB", "2026-11-03", title="Skole-hjem-samtaler"),
    ])
    answer = _slots(ctx, {("2026-11-03", "ChildB"): [
        {"refs": ["fact-2"], "priority": "event", "text": "Skole-hjem-samtale"}]}, [
        {"ref": "fact-1", "reason": "duplicate", "same_as": "fact-2"},  # the other child's line: shown
        {"ref": "fact-3", "reason": "duplicate", "same_as": "fact-2"},  # same child: accepted
    ])
    state, audit = build_state(ctx, answer)
    reasons = {item["ref"]: (item["decision"], item["reason"]) for item in audit["facts"]}
    assert state["weekdays"][1]["children"][0]["lines"] == ["Skole-hjem-samtale kl. 15"]
    assert state["weekdays"][1]["children"][0]["kinds"] == ["meeting"]
    assert reasons["fact-3"] == ("omitted", "Duplicate of fact-2")


def test_builder_turns_too_many_lines_into_a_count_and_repairs_headings():
    from tests_python.test_display_services import _slots
    items = [fact(f"fact-{n}", "ChildA", "2026-11-03", "event", title=f"Punkt {n}") for n in range(1, 6)]
    ctx = context("2026-11-02T08:00:00", items)
    lines = [{"refs": [f"fact-{n}"], "priority": "event", "text": f"Punkt {n}"} for n in range(1, 6)]
    answer = _slots(ctx, {("2026-11-03", "ChildA"): lines})
    answer["weekdays"][1]["children"][0]["heading"] = "Tirsdag"
    state, audit = build_state(ctx, answer)
    row = state["weekdays"][1]["children"][0]
    assert row["lines"] == ["Punkt 1", "Punkt 2", "+3 punkter i skolebriefen"]
    assert row["heading"] == "Arrangement"
    assert sum(item["reason"] == "Board capacity" for item in audit["facts"]) == 3


def test_fallback_board_is_valid_puts_obligations_first_and_says_it_is_plain():
    items = [
        fact("fact-1", "ChildA", "2026-11-06", "event", title="Teaterbesøg"),
        fact("fact-2", "ChildA", "2026-11-06", "bring", title="Madpakke", actionability="required"),
        fact("fact-3", "ChildA", "2026-11-06", "homework", title="Læs side 12", actionability="required"),
        fact("fact-4", "ChildA", "2026-11-06", "event", title="5. klasse på museum", needs_review=True),
        fact("fact-5", "household", "2026-11-06", "schedule_change", title="Morgensang er aflyst"),
        fact("fact-6", "ChildB", "2026-11-02", "parent_action", title="Tilmeld samtale",
             actionability="recommended"),
        *[fact(f"fact-{n}", "ChildB", "2026-11-03", "substitute",
               title="Alex Winther er vikar i MAT · 02") for n in (7, 8)],
        *[fact(f"fact-{n}", "ChildB", "2026-11-04", "event", title=f"Punkt {n}") for n in range(9, 14)],
        fact("fact-14", "ChildA", "2026-11-12", title="Næste uge"),
        party("fact-15", "ChildB", "2026-11-07", 3),
    ]
    ctx = context("2026-11-05T19:35:00", items)
    state, audit = fallback_composition(ctx)
    validate_composition(ctx, state, audit)
    assert state["horizon_label"] == "Skoleugen 2.–6. nov. · enkel visning"
    assert state["hero"]["day"] == "Fredag 6. nov." and state["hero"]["state"] == "Næste skoledag"
    assert state["hero"]["children"][0]["lines"] == ["Medbring: Madpakke", "Læs side 12", "Teaterbesøg"]
    assert state["hero"]["children"][0]["heading"] == "Medbring og lektier"
    friday = state["weekdays"][4]
    assert friday["shared"] == "Morgensang er aflyst"
    assert state["weekdays"][1]["children"][1]["lines"] == ["Vikar: Alex W. · MAT 02"]
    assert state["weekdays"][2]["children"][1]["lines"][-1] == "+3 punkter i skolebriefen"
    assert state["weekdays"][0]["children"][1]["lines"] == ["Valgfrit: Tilmeld samtale"]
    assert state["weekend"] == [{"day": "Lør. 7. nov.", "text": "ChildB: Fødselsdagsfest", "kinds": ["party"]}]
    decisions = {item["ref"]: item for item in audit["facts"]}
    assert decisions["fact-4"]["reason"] == "Needs review"
    assert decisions["fact-14"]["reason"] == "Outside displayed week"
    assert decisions["fact-8"]["decision"] == "merged"


def test_validation_reason_names_well_formed_facts_only():
    error = RuleViolation("private event row must include its retained bring-item",
                          ["fact-21", "fact-22 at weekend[1]", "Anna R. at hero", "fact-3 at hero.children[0].lines[2]"])
    assert validation_reason(error) == (
        "private event row must include its retained bring-item "
        "[fact-21; fact-22 at weekend[1]; fact-3 at hero.children[0].lines[2]]")


def _frames(monkeypatch, compose_week, items):
    monkeypatch.setenv("AG_WEATHER", "off")
    now = local("2026-11-05T19:35:00")

    def at(moment):
        return {**context_at(moment, updated="2026-11-05T19:31:00"), "items": items}
    return scan_service._dashboard_frames(now, at, compose_week, {}, "model", {"name": ""})


def test_scan_publishes_a_fallback_board_when_composition_fails(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="agskoleintra.cloud_job")

    def failing(context):
        raise CompositionFailed("Dashboard composition failed validation: x",
                                ["birthday party must retain its activity kind [fact-1 at weekend[0]]"] * 6)

    frames, weeks, _ = _frames(monkeypatch, failing, [party("fact-1", "ChildB", "2026-11-07", 3)])
    assert all(week["fallback"] for week in weeks.values())
    assert frames[0][1]["horizon_label"].endswith("enkel visning")
    events = [json.loads(r.message) for r in caplog.records if '"composition_fallback"' in r.message]
    assert events and events[0]["failure_code"] == "composition_validation"
    assert events[0]["attempts"][0].endswith("[fact-1 at weekend[0]]")


def test_scan_keeps_the_composition_error_when_the_fallback_fails_too(monkeypatch):
    def failing(context):
        raise CompositionFailed("Dashboard composition failed validation: x", ["reason"] * 6)

    monkeypatch.setattr("skoleklar.dashboard_build.fallback_composition",
                        lambda context: (_ for _ in ()).throw(KeyError("items")))
    with pytest.raises(CompositionFailed):
        _frames(monkeypatch, failing, [])


def test_a_fact_spanning_days_may_sit_on_any_of_them_but_not_outside():
    from tests_python.test_display_services import _slots
    week_fact = fact("fact-1", "household", "2026-11-02", "context", title="Praktikant i uge 45",
                     end_date="2026-11-08")
    ctx = context("2026-11-05T19:35:00", [week_fact])
    friday = _slots(ctx)
    friday["weekdays"][4]["shared"] = {"refs": ["fact-1"], "priority": "context", "text": "Praktikant i skolen"}
    state, audit = build_state(ctx, friday)
    assert state["weekdays"][4]["shared"] == "Praktikant i skolen"
    validate_composition(ctx, state, audit)
    single = fact("fact-1", "ChildA", "2026-11-02", "event", title="Teatertur")
    ctx = context("2026-11-05T19:35:00", [single])
    misplaced = {"source_updated_at": ctx["freshness"]["source_updated_at"], "facts": [
        {"ref": "fact-1", "decision": "displayed", "location": "weekdays[4].children[0].lines[0]", "reason": "x"}]}
    with pytest.raises(RuleViolation, match="displayed fact date must match"):
        validate_composition(ctx, build_state(ctx, _slots(ctx))[0], misplaced)
