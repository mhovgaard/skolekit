import copy
import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from skoleklar.dashboard_timing import dashboard_focus
from skolekit.records import SourceCheck, SourceRecord
from skoleklar.storage import SnapshotStore
from tests_python.board_lines import freshness, line, week_state

# This refactor's core set: `pytest -m pipeline` (docs/maintenance.md).
pytestmark = pytest.mark.pipeline

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


dashboard = module("rendering/shared/dashboard.py", "school_dashboard")
context = module("rendering/ag-school-dashboard-state/scripts/prepare_context.py", "school_context")
composition = module("rendering/ag-school-dashboard-state/scripts/validate_state.py", "school_composition")


def state():
    """A composed week (schema 3) with one line for the first child's hero."""
    value = weekday_grid_state()
    value["hero"]["children"][0]["lines"] = [line("Bog", "homework", day="2026-09-21")]
    return value


def weekday_grid_state(composed="2026-09-21T08:00:00+02:00", *, dense=False):
    # The week of 21 September; its hero is the frame's next school day when that is in it.
    focus = dashboard_focus(datetime.fromisoformat(composed)).school_date.isoformat()
    value = week_state("2026-09-21", hero=focus if "2026-09-21" <= focus <= "2026-09-25" else None,
                       composed=composed)
    value["weekend"] = [{"date": "2026-09-26", "day": "Lør. 26. sep.", "who": "Begge",
                         "lines": [line("Familietur kl. 11", "trip", day="2026-09-26")]}]
    for day in value["weekdays"]:
        for child in day["children"]:
            child["lines"] = [line(f"Punkt {index}", day=day["date"]) for index in (1, 2, 3)] if dense else []
    return value


@pytest.mark.parametrize("key", ["subject", "body", "thread_ref", "message_thread_ref", "source_url", "extra"])
def test_hidden_private_fields_are_rejected(key):
    value = state()
    value["messages"]["items"][0][key] = "private canary"
    with pytest.raises(ValueError):
        dashboard.render_html(value)


def plant_legacy_message(path, body: str) -> None:
    """A database as the removed legacy mode left it: a message stored as a record."""
    with SnapshotStore(path) as store:
        run = store.begin_run()
        store.store_checks(run, [SourceCheck(student_id=1, child="ChildA", source_type="frontpage",
                                            records=[SourceRecord(
                                                student_id=1, child="ChildA", source_type="frontpage",
                                                source_key="thread:1", source_url="https://example.test/private",
                                                title="SECRET SUBJECT", body_text=body, raw_payload={})],
                                            complete=True)])
        store.connection.execute("UPDATE records SET source_type = 'messages'")
        store.connection.commit()


def test_html_escapes_text_and_keeps_the_aggregate(tmp_path):
    value = state()
    value["freshness"]["expires_at"] = "2099-09-22T20:00:00+02:00"
    value["hero"]["children"][0]["lines"] = [line("<script>alert(1)</script>", day="2026-09-21")]
    path = tmp_path / "state.json"
    path.write_text(json.dumps(value))
    result = subprocess.run([sys.executable, str(ROOT / "rendering/ag-school-html-renderer/scripts/render_html.py"),
                             str(path), "--output-dir", str(tmp_path / "html")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    html = (tmp_path / "html/dashboard.html").read_text()
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    # The only script is the constant, CSP-hashed fold script; state never enters it.
    assert html.count("<script>") == 1 and "alert(1)" not in html.split("<script>")[1]
    assert "7 ulæste" in html
    assert not list((tmp_path / "html").glob("*.png"))


def test_labels_come_from_the_line_never_its_words():
    value = weekday_grid_state()
    tuesday = "2026-09-22"
    value["weekdays"][1]["children"][1]["lines"] = [
        line("turtaske og drikkedunk", "optional", day=tuesday),
        line("Mette · MAT", "substitute", day=tuesday),
        line("s. 25–26", "homework", day=tuesday, school_subject="Matematik"),
    ]
    html = dashboard.render_html(value)
    # Nothing on the parents' board is clipped; Tuesday is in its look-ahead.
    assert "overflow:hidden" not in html.split("</style>")[1]
    assert "<b>Valgfrit</b> turtaske og drikkedunk" in html
    assert "<b>Vikar</b> Mette · MAT" in html
    assert '<span class="box"></span><b>Mat.</b> s. 25–26' in html
    # Words that look like a label are only words.
    value["weekdays"][1]["children"][1]["lines"] = [line("Medbring: bamse", day=tuesday)]
    html = dashboard.render_html(value)
    assert "Medbring: bamse" in html and "<b>Medbring</b>" not in html


def test_a_weekly_routine_is_never_drawn():
    value = weekday_grid_state()
    value["weekdays"][1]["children"][0]["lines"] = [line("Morgensang", "routine", day="2026-09-22")]
    assert "Morgensang" not in dashboard.render_html(value)
    value["weekdays"][1]["children"][0]["lines"] = [line("Morgensang", "event", day="2026-09-22")]
    assert "Morgensang" in dashboard.render_html(value)


def attention_state():
    value = weekday_grid_state()
    # A deadline today is time-urgent: the one thing that earns the yellow marker.
    value["hero"]["children"][0]["lines"] = [line("aflever samtykke til turen", "deadline", day="2026-09-21")]
    value["weekdays"][0]["shared"] = [line("forældremøde kl. 17", "meeting", day="2026-09-21")]
    value["day_weather"] = [
        {"date": day["date"], "icon": icon, "low": 9, "high": 14, "rain_mm": 3}
        for day, icon in zip(value["weekdays"], ("rain", "sun", "partly", "cloud", "fog"))
    ]
    return value


@pytest.mark.parametrize("change", [
    {"label": "Husk"}, {"chip": "FEST!"}, {"kind": "party_time"}, {"refs": ["thread:1"]},
    {"urgent": {"from": "2026-09-22", "to": "2026-09-21"}}, {"box": 1}, {"extra": True},
])
def test_a_board_line_is_allowlisted(change):
    value = state()
    value["hero"]["children"][0]["lines"][0].update(change)
    with pytest.raises(ValueError):
        dashboard.validate(value)


def test_only_schema_3_renders():
    value = state()
    value["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version must be 3"):
        dashboard.validate(value)


def _packet(value, items, now=None):
    packet = {"items": items, "freshness": value["freshness"],
              "messages": copy.deepcopy(value["messages"]), "children": ["ChildA", "ChildB"]}
    if now:
        packet["now"] = now
    return packet


def test_composition_rejects_lost_fact_or_false_unread_total():
    value = weekday_grid_state()
    value["weekend"] = []
    packet = _packet(value, [{"ref": "fact-1"}])
    audit = {"source_updated_at": value["freshness"]["source_updated_at"], "facts": []}
    with pytest.raises(ValueError, match="exactly once"):
        composition.validate_composition(packet, value, audit)
    audit["facts"] = [{"ref": "fact-1", "decision": "omitted", "reason": "uncertain"}]
    composition.validate_composition(packet, value, audit)
    value["messages"]["unread_count"] = 1
    with pytest.raises(ValueError, match="aggregate counts"):
        composition.validate_composition(packet, value, audit)


def test_composition_checks_references_and_presentation_not_wording():
    value = weekday_grid_state()
    value["weekend"] = []
    fact = {"ref": "fact-1", "type": "bring", "child": "ChildA", "actionability": "required",
            "effective_date": "2026-09-21"}
    packet = _packet(value, [fact], now="2026-09-21T08:00:00+02:00")
    audit = {"source_updated_at": value["freshness"]["source_updated_at"],
             "facts": [{"ref": "fact-1", "decision": "displayed",
                        "location": "hero.children[0].lines[0]", "reason": "selected"}]}
    # Any wording passes: the bold `Medbring` comes from the fact, not the text.
    value["hero"]["children"][0]["lines"] = [line("bevægeligt tøj", "bring", day="2026-09-21")]
    composition.validate_composition(packet, value, audit)
    # A line that looks like something its fact is not is rejected.
    value["hero"]["children"][0]["lines"] = [line("bevægeligt tøj", "optional", day="2026-09-21")]
    with pytest.raises(ValueError, match="look as its facts say"):
        composition.validate_composition(packet, value, audit)
    # So is a reference to a fact the context does not have.
    value["hero"]["children"][0]["lines"] = [line("tøj", "bring", day="2026-09-21", ref="fact-9")]
    with pytest.raises(ValueError, match="unknown fact"):
        composition.validate_composition(packet, value, audit)
    # And an audit that names a line the fact is not on.
    value["hero"]["children"][0]["lines"] = [line("tøj", "bring", day="2026-09-21", ref="fact-1")]
    audit["facts"][0]["location"] = "hero.children[1].lines[0]"
    with pytest.raises(ValueError, match="line its audit names"):
        composition.validate_composition(packet, value, audit)


def test_composition_merges_private_event_with_retained_bring_detail():
    value = weekday_grid_state("2026-09-24T10:00:00+02:00")
    event = {"ref": "fact-2", "type": "event", "child": "ChildA", "effective_date": "2026-09-26",
             "private_message_fact": True, "actionability": "informational", "message_thread": 1}
    bring = {"ref": "fact-1", "type": "bring", "child": "ChildA", "effective_date": "2026-09-26",
             "private_message_fact": True, "actionability": "recommended", "message_thread": 1}
    value["weekend"] = [{"date": "2026-09-26", "day": "Lør. 26. sep.", "who": "ChildA",
                         "lines": [line("Arrangement", "event", day="2026-09-26", ref="fact-2")]}]
    packet = _packet(value, [bring, event], now="2026-09-24T10:00:00+02:00")
    audit = {"source_updated_at": value["freshness"]["source_updated_at"], "facts": [
        {"ref": "fact-1", "decision": "omitted", "reason": "row limit"},
        {"ref": "fact-2", "decision": "displayed", "location": "weekend[0]", "reason": "selected"},
    ]}
    with pytest.raises(ValueError, match="include its retained bring-item"):
        composition.validate_composition(packet, value, audit)
    value["weekend"][0]["lines"].append(line("brætspil", "optional", day="2026-09-26", ref="fact-1"))
    audit["facts"][0] = {"ref": "fact-1", "decision": "merged", "location": "weekend[0]",
                         "reason": "same message row"}
    composition.validate_composition(packet, value, audit)


def test_composition_rejects_fact_on_wrong_dashboard_date():
    value = weekday_grid_state("2026-09-25T23:00:00+02:00")
    value["weekend"] = [{"date": "2026-09-27", "day": "Søn. 27. sep.", "who": "Begge",
                         "lines": [line("Aktivitet", "event", day="2026-09-27")]}]
    value = {**value, **{key: weekday_grid_state("2026-09-28T08:00:00+02:00")[key]
                         for key in ("hero", "weekdays")}}
    for day in value["weekdays"]:
        day["date"] = (datetime.fromisoformat(day["date"]) + timedelta(days=7)).date().isoformat()
    value["hero"]["date"] = "2026-09-28"
    packet = _packet(value, [{"ref": "fact-1", "type": "event", "effective_date": "2026-10-04"}],
                     now="2026-09-25T23:00:00+02:00")
    audit = {"source_updated_at": value["freshness"]["source_updated_at"],
             "facts": [{"ref": "fact-1", "decision": "displayed", "location": "weekend[0]",
                        "reason": "selected"}]}
    with pytest.raises(ValueError, match="fact date must match"):
        composition.validate_composition(packet, value, audit)


def test_validation_holds_no_danish_word_patterns():
    source = (ROOT / "src/skoleklar/dashboard_validation.py").read_text()
    for word in ("medbring", "valgfri", "besked", "vikar", "aflyst", "klassetur", "fødselsdag",
                 "mandag", "læs", "husk"):
        assert word not in source.casefold(), word
