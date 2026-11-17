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


def test_png_uses_exact_spectra6_codes_and_nominal_palette():
    image = dashboard.render_png(weekday_grid_state())
    dashboard.validate_panel_image(image)
    used = {index for _count, index in image.getcolors(maxcolors=256)}
    assert used <= set(dashboard.PANEL_DESIGN_CODES)
    palette = image.getpalette()
    for code, rgb in dashboard.PANEL_NOMINAL_RGB.items():
        assert tuple(palette[code * 3:code * 3 + 3]) == rgb


def test_neutral_text_edges_never_gain_colour_fringes():
    from PIL import Image

    greys = Image.new("RGB", (800, 480))
    values = [(level, level, level) for level in range(256)]
    greys.putdata((values * 1500)[:800 * 480])
    result = dashboard.quantize_for_panel(greys)
    assert {index for _count, index in result.getcolors(256)} == {0, 1}


@pytest.mark.parametrize("mode", dashboard.DITHER_MODES)
def test_each_dither_profile_produces_a_valid_device_frame(mode):
    image = dashboard.render_png(weekday_grid_state(), dither_mode=mode)
    dashboard.validate_panel_image(image)
    used = {index for _count, index in image.getcolors(maxcolors=256)}
    assert 0x4 not in used
    assert len(dashboard.pack_panel_frame(image)) == 192_000


def test_hybrid_dithers_colour_surfaces_but_hard_maps_protected_pixels():
    from PIL import Image, ImageDraw

    source = Image.new("RGB", (800, 480), dashboard.HTML_PALETTE["ICE"])
    protected = Image.new("L", source.size, 0)
    ImageDraw.Draw(protected).rectangle((0, 0, 199, 479), fill=255)
    crisp = dashboard.quantize_for_panel(source, mode="crisp")
    hybrid = dashboard.quantize_for_panel(source, mode="hybrid", protected_mask=protected)

    assert set(hybrid.crop((0, 0, 200, 480)).get_flattened_data()) == {0x1}
    assert len(set(hybrid.crop((200, 0, 800, 480)).get_flattened_data())) > 1
    assert hybrid.tobytes() != crisp.tobytes()


def test_panel_preview_replaces_codes_with_appearance_values():
    from PIL import Image

    panel = Image.frombytes("P", (800, 480), bytes([0, 1, 2, 3, 5, 6]) * 64000)
    panel.putpalette(dashboard.panel_palette())
    preview = dashboard.render_panel_preview(panel)
    assert preview.mode == "RGB"
    assert preview.size == (800, 480)
    assert list(preview.get_flattened_data())[:6] == [
        dashboard.PANEL_APPEARANCE_RGB[code] for code in (0, 1, 2, 3, 5, 6)
    ]


def test_panel_frame_packs_left_pixel_in_high_nibble():
    from PIL import Image

    panel = Image.frombytes("P", (800, 480), bytes([0, 1, 2, 3, 5, 6]) * 64000)
    panel.putpalette(dashboard.panel_palette())
    frame = dashboard.pack_panel_frame(panel)
    assert len(frame) == 192_000
    assert frame[:3] == bytes((0x01, 0x23, 0x56))


def test_message_participant_accepts_an_uppercase_unicode_initial():
    value = state()
    value["messages"]["items"][0]["participant"] = "Name Þ."
    dashboard.validate(value)
    value["messages"]["items"][0]["participant"] = "Name þ."
    with pytest.raises(ValueError, match="participant must be shortened"):
        dashboard.validate(value)


def test_weekend_activity_after_the_look_ahead_is_a_later_event():
    value = weekday_grid_state()
    html = dashboard.render_html(value)
    # Saturday is after Monday's four-day look-ahead, so it is listed below the rule.
    assert '<span class="chip">TUR</span>Familietur kl. 11' in html
    assert html.index("pb-rule") < html.index("Familietur kl. 11")
    image = dashboard.render_png(value)
    assert image.size == (800, 480)
    assert image.mode == "P"
    assert len(image.getcolors()) <= 6


@pytest.mark.parametrize(
    ("moment", "mode", "school_date"),
    [
        ("2026-09-21T12:00:00+02:00", "schoolday", "2026-09-21"),
        ("2026-09-21T14:59:59+02:00", "schoolday", "2026-09-21"),
        ("2026-09-21T15:00:00+02:00", "schoolday", "2026-09-22"),
        ("2026-09-25T14:59:59+02:00", "schoolday", "2026-09-25"),
        ("2026-09-25T15:00:00+02:00", "weekend", "2026-09-28"),
        ("2026-09-26T10:00:00+02:00", "weekend", "2026-09-28"),
        ("2026-09-27T11:59:59+02:00", "weekend", "2026-09-28"),
        ("2026-09-27T12:00:00+02:00", "schoolday", "2026-09-28"),
    ],
)
def test_dashboard_focus_uses_the_15_clock_and_weekend_policy(moment, mode, school_date):
    focus = dashboard_focus(datetime.fromisoformat(moment))
    assert focus.mode == mode
    assert focus.school_date.isoformat() == school_date


def test_new_messages_are_the_last_day_or_the_whole_weekend():
    from skoleklar.dashboard_context import arrived_since
    from skoleklar.dashboard_timing import new_messages_since

    at = datetime.fromisoformat
    # On school days: the last 24 hours.
    assert new_messages_since(at("2026-09-23T07:00:00+02:00")) == at("2026-09-22T07:00:00+02:00")
    assert new_messages_since(at("2026-09-27T12:00:00+02:00")) == at("2026-09-26T12:00:00+02:00")
    # At the weekend: everything since Friday 15:00, and still 24 hours on Friday evening.
    assert new_messages_since(at("2026-09-25T19:00:00+02:00")) == at("2026-09-24T19:00:00+02:00")
    since = new_messages_since(at("2026-09-27T09:00:00+02:00"))
    assert since == at("2026-09-25T15:00:00+02:00")
    assert arrived_since("2026-09-25T15:00", since) and arrived_since("2026-09-26", since)
    assert not arrived_since("2026-09-25T14:59", since)
    # A bare date counts from the cutoff's own day, so an evening message is never missed.
    assert arrived_since("2026-09-25", since) and not arrived_since("2026-09-24", since)
    assert not arrived_since(None, since)


def test_inbox_total_is_in_the_begge_row():
    value = weekday_grid_state()
    value["freshness"]["expires_at"] = "2099-09-22T20:00:00+02:00"
    html = dashboard.render_html(value)
    assert html.index("pb-begge-l") < html.index("7 ulæste i alt")
    value["messages"]["new_count"] = -1
    with pytest.raises(ValueError):
        dashboard.validate(value)


def test_html_uses_the_aizome_palette_and_no_panel_colours():
    html = dashboard.render_html(attention_state())
    for colour in dashboard.HTML_PALETTE.values():
        assert colour in html
    for panel_colour in ("#191E21", "#E8E8E8", "#EFDE44", "#B21318", "#2157BA", "#125F20"):
        assert panel_colour not in html


@pytest.mark.parametrize("mode", dashboard.DITHER_MODES)
def test_red_and_green_pigments_never_reach_the_panel(mode):
    image = dashboard.render_png(attention_state(), dither_mode=mode)
    used = {index for _count, index in image.getcolors(maxcolors=256)}
    assert used <= set(dashboard.PANEL_DESIGN_CODES)
    assert 0x2 in used  # the attention marker


def test_day_weather_gives_a_heads_up_beside_the_date():
    value = attention_state()
    html = dashboard.render_html(value)
    # Monday has 3 mm of rain: the temperatures and what to wear, beside the date.
    assert 'class="pb-wx"' in html and "9–14° Regntøj" in html
    plain, with_weather = (dashboard.render_png(state) for state in (weekday_grid_state(), value))
    assert plain.crop((180, 4, 326, 40)).tobytes() != with_weather.crop((180, 4, 326, 40)).tobytes()


@pytest.mark.parametrize("change", [
    {"icon": "bike"}, {"low": 9.5}, {"high": 99}, {"rain_mm": -1}, {"date": "i dag"}, {"note": "x"},
])
def test_day_weather_is_allowlisted(change):
    value = attention_state()
    value["day_weather"][0].update(change)
    with pytest.raises(ValueError):
        dashboard.validate(value)


def test_read_only_snapshot_refuses_writes_and_missing_database(tmp_path):
    path = tmp_path / "snapshot.db"
    with SnapshotStore(path):
        pass
    before = path.read_bytes()
    with SnapshotStore(path, read_only=True) as store:
        with pytest.raises(sqlite3.OperationalError):
            store.begin_run()
    assert path.read_bytes() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        SnapshotStore(missing, read_only=True)
    assert not missing.exists()


def test_export_missing_inbox_coverage_does_not_claim_checked(tmp_path):
    path = tmp_path / "snapshot.db"
    with SnapshotStore(path) as store:
        run = store.begin_run()
        store.store_checks(run, [SourceCheck(student_id=1, child="ChildA", source_type="calendar",
                                            records=[], complete=True)])
    before = path.read_bytes()
    with SnapshotStore(path, read_only=True) as store:
        packet = context.export_context(store, now=datetime.now(timezone.utc))
    assert packet["messages"]["checked"] is False
    assert path.read_bytes() == before


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


def test_export_refuses_legacy_message_database(tmp_path):
    path = tmp_path / "legacy.db"
    plant_legacy_message(path, "SECRET BODY")
    with pytest.raises(ValueError, match="removed legacy mode"):
        SnapshotStore(path, read_only=True)

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


def test_dense_next_day_is_drawn_in_full_without_shortening(monkeypatch):
    from skoleklar import parents_board_render
    value = weekday_grid_state()
    day = "2026-09-21"
    value["hero"]["children"][1]["lines"] = [
        line("idrætstøj, indendørssko", "bring", day=day), line("håndklæde og sæbe", "bring", day=day),
        line("hårelastik ved langt hår", "bring", day=day),
        line("Færdiggør HammerRegn s. 1-10; læs s. 80-81 og lav s. 40", "homework", day=day),
        line("tilmeld skolehjemssamtale", "deadline", day=day),
    ]
    drawn = []
    original = parents_board_render._text
    monkeypatch.setattr(parents_board_render, "_text",
                        lambda draw, x, y, text, *a: drawn.append(text) or original(draw, x, y, text, *a))
    dashboard.render_png(value)
    assert not any(text.endswith("…") for text in drawn)
    joined = " ".join(drawn)
    assert "skolehjemssamtale" in joined and "hårelastik ved langt hår" in joined


def test_the_hero_holds_at_most_five_lines_per_child():
    value = weekday_grid_state()
    value["hero"]["children"][1]["lines"] = [line(f"Punkt {index}", day="2026-09-21") for index in range(5)]
    dashboard.validate(value)
    assert dashboard.render_png(value).size == (800, 480)
    value["hero"]["children"][1]["lines"].append(line("For meget", day="2026-09-21"))
    with pytest.raises(ValueError, match="too many child lines"):
        dashboard.validate(value)


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


def test_stale_data_never_claims_current_inbox_count():
    value = state()
    now = datetime.now(timezone.utc)
    value["freshness"] = {"source_updated_at": (now-timedelta(days=3)).isoformat(),
                          "composed_at": now.isoformat(), "expires_at": (now-timedelta(days=1)).isoformat(),
                          "status": "fresh"}
    html = dashboard.render_html(value)
    assert "ÆLDRE DATA" in html
    assert "Beskeder ikke tjekket" in html
    assert "7 ulæste" not in html


def test_overflow_and_changed_child_order_are_rejected():
    value = state()
    value["hero"]["children"][0]["lines"] = [line("x", day="2026-09-21")] * 6
    with pytest.raises(ValueError):
        dashboard.validate(value)
    value = state()
    value["weekdays"][1]["children"].reverse()
    with pytest.raises(ValueError, match="child order"):
        dashboard.validate(value)


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
