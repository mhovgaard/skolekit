import copy
import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from skoleklar.dashboard_composition import _compact_state
from skoleklar.dashboard_timing import dashboard_focus
from skolekit.records import SourceCheck, SourceRecord
from skoleklar.storage import SnapshotStore

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
    return {
        "schema_version": 1, "week_label": "UGE 1", "updated_label": "DATA 1. JAN",
        "hero": {"state": "I DAG", "day": "Mandag 1.", "children": [
            {"name": "ChildA", "heading": "Skolebog", "lines": ["Bog"]},
            {"name": "ChildB", "heading": "Ingen punkter", "lines": []},
        ]},
        "horizon": [],
        "messages": {"checked": True, "bodies_retained": 0, "unread_count": 7,
                     "items": [{"participant": "Teacher T.", "children": ["ChildA"],
                                "activity": "1. jan", "unread": True}]},
    }


def five_day_weekend_state():
    value = state()
    value["hero"] = {"state": "NÆSTE SKOLEDAG", "day": "Mandag 21.", "children": [
        {"name": "ChildA", "heading": "Læsning", "lines": ["Læs side 12"]},
        {"name": "ChildB", "heading": "Vikar", "lines": ["Alex A. · MAT · 2.A"]},
    ]}
    value["weekend"] = [{"day": "Søn. 20.", "text": "Familieaktivitet kl. 11"}]
    value["horizon"] = [
        {"day": day, "children": [
            {"name": "ChildA", "heading": "Ingen punkter", "lines": []},
            {"name": "ChildB", "heading": "Læsning", "lines": ["Læs side 14"]},
        ], "shared": None}
        for day in ("Tir. 22.", "Ons. 23.", "Tor. 24.")
    ]
    value["later"] = [
        {"day": "Fre. 25.", "summary": "Ingen registrerede punkter"},
        {"day": "Lør. 26.", "summary": "Fodbold kl. 10"},
    ]
    return value


def weekday_grid_state(composed="2026-09-21T08:00:00+02:00", *, dense=False):
    value = state()
    value["schema_version"] = 2
    value.pop("horizon")
    value["hero"]["day"] = "Mandag 21. sep."
    value["weekend"] = [{"day": "Lør. 26. sep.", "text": "Familietur kl. 11"}]
    value["horizon_label"] = "Skoledage"
    value["past_summary"] = "Uge 39"
    value["footer"] = ""
    value["freshness"] = {
        "source_updated_at": composed,
        "composed_at": composed,
        "expires_at": (datetime.fromisoformat(composed) + timedelta(hours=36)).isoformat(),
        "status": "fresh",
    }
    names = [child["name"] for child in value["hero"]["children"]]
    value["weekdays"] = []
    for offset, label in enumerate(("Man. 21. sep.", "Tir. 22. sep.", "Ons. 23. sep.",
                                    "Tor. 24. sep.", "Fre. 25. sep.")):
        lines = ["Punkt 1", "Punkt 2", "Punkt 3"] if dense else []
        value["weekdays"].append({
            "date": f"2026-09-{21 + offset:02d}",
            "day": label,
            "children": [
                {"name": names[0], "heading": "Skole", "lines": list(lines)},
                {"name": names[1], "heading": "Skole", "lines": list(lines)},
            ],
            "shared": None,
            "summary": "Skolepunkter",
        })
    return value


@pytest.mark.parametrize("key", ["subject", "body", "thread_ref", "message_thread_ref", "source_url", "extra"])
def test_hidden_private_fields_are_rejected(key):
    value = state()
    value["messages"]["items"][0][key] = "private canary"
    with pytest.raises(ValueError):
        dashboard.render_html(value)


def test_html_escapes_text_preserves_aggregate_and_needs_no_pillow(tmp_path):
    value = state()
    value["hero"]["children"][0]["lines"] = ["<script>alert(1)</script>"]
    path = tmp_path / "state.json"
    path.write_text(json.dumps(value))
    result = subprocess.run([sys.executable, "-S", str(ROOT / "rendering/ag-school-html-renderer/scripts/render_html.py"),
                             str(path), "--output-dir", str(tmp_path / "html")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    html = (tmp_path / "html/dashboard.html").read_text()
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    # The only script is the constant, CSP-hashed fold script; state never enters it.
    assert html.count("<script>") == 1 and "alert(1)" not in html.split("<script>")[1]
    assert "7 ulæste" in html
    assert not list((tmp_path / "html").glob("*.png"))


def test_weekend_hero_and_five_day_lookahead_render_compactly():
    value = five_day_weekend_state()
    html = dashboard.render_html(value)
    assert "NÆSTE SKOLEDAG" in html
    assert "Familieaktivitet kl. 11" in html
    assert "Derefter" in html and "Fre. 25." in html and "Lør. 26." in html
    assert "@container (max-width:680px)" in html
    image = dashboard.render_png(value)
    assert image.size == (800, 480)
    assert image.mode == "P"
    assert len(image.getcolors()) <= 6


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


def test_weekday_grid_unfolds_when_sparse_and_folds_farthest_when_dense():
    sparse = weekday_grid_state()
    assert not any(item["folded"] for item in dashboard.weekday_layout(sparse))

    monday = weekday_grid_state(dense=True)
    friday = weekday_grid_state("2026-09-25T08:00:00+02:00", dense=True)
    assert [item["folded"] for item in dashboard.weekday_layout(monday)] == [False, False, False, False, True]
    # Days that are already over always fold (Mon-Wed); Thu is pinned as the nearest
    # remaining day, Fri is the only upcoming day.
    assert [item["folded"] for item in dashboard.weekday_layout(friday)] == [True, True, True, False, False]


def test_panel_weekday_rows_never_run_into_the_weekend_section():
    from PIL import Image, ImageDraw
    value = weekday_grid_state()
    for day in value["weekdays"]:
        for child in day["children"]:
            child["lines"] = [f"Punkt {index} med en lang linje der ombrydes over to rækker"
                              for index in range(3)]
        day["shared"] = "Begge: en fælles besked der også fylder to hele rækker i tabellen"
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480)))
    layout = dashboard.eink_weekday_layout(value, draw)
    assert sum(item["height"] for item in layout) <= 309
    # The hero day folds first: its detail is already in full on the left.
    assert layout[0]["folded"] and not layout[1]["folded"]


def test_dense_next_day_is_drawn_in_full_without_shortening(monkeypatch):
    from skoleklar import parents_board_render
    value = weekday_grid_state()
    value["hero"]["children"][1]["lines"] = [
        "Medbring: idrætstøj, indendørssko", "Medbring: håndklæde og sæbe",
        "Medbring: hårelastik ved langt hår",
        "Færdiggør HammerRegn s. 1-10; læs s. 80-81 og lav s. 40",
        "Tilmeld skolehjemssamtale",
    ]
    drawn = []
    original = parents_board_render._text
    monkeypatch.setattr(parents_board_render, "_text",
                        lambda draw, x, y, text, *a: drawn.append(text) or original(draw, x, y, text, *a))
    dashboard.render_png(value)
    assert not any(text.endswith("…") for text in drawn)
    joined = " ".join(drawn)
    assert "skolehjemssamtale" in joined and "hårelastik ved langt hår" in joined


def test_schema_v2_hero_accepts_five_dense_next_day_lines():
    value = weekday_grid_state()
    value["hero"]["children"][1]["lines"] = [f"Punkt {index}" for index in range(5)]
    dashboard.validate(value)
    assert dashboard.render_png(value).size == (800, 480)
    value["hero"]["children"][1]["lines"].append("For meget")
    with pytest.raises(ValueError, match="too many child lines"):
        dashboard.validate(value)


def test_message_participant_accepts_an_uppercase_unicode_initial():
    value = state()
    value["messages"]["items"][0]["participant"] = "Name Þ."
    dashboard.validate(value)
    value["messages"]["items"][0]["participant"] = "Name þ."
    with pytest.raises(ValueError, match="participant must be shortened"):
        dashboard.validate(value)


def test_five_moderately_filled_weekdays_stay_unfolded():
    value = weekday_grid_state()
    line_counts = (2, 2, 2, 1, 3)
    for day, count in zip(value["weekdays"], line_counts):
        day["children"][1]["lines"] = [f"Punkt {index}" for index in range(count)]
    assert not any(item["folded"] for item in dashboard.weekday_layout(value))
    assert "day folded" not in dashboard.render_html(value)


def test_wrapped_weekday_lines_are_counted_in_layout_instead_of_hidden():
    value = weekday_grid_state()
    long_lines = [
        "Hvis ugens kammerat: Medbring bamse",
        "Valgfrit: turtaske og drikkedunk",
        "Morgensang; tur på biblioteket",
    ]
    value["weekdays"][1]["children"][1]["lines"] = long_lines

    layout = dashboard.weekday_layout(value)
    assert not any(item["folded"] for item in layout)
    assert layout[1]["height"] >= 85
    assert sum(item["height"] for item in layout) <= 309

    html = dashboard.render_html(value)
    # Nothing on the parents' board is clipped; Tuesday is in its look-ahead.
    assert "overflow:hidden" not in html.split("</style>")[1]
    assert "<b>Valgfrit</b> turtaske og drikkedunk" in html

    for day in value["weekdays"]:
        day["children"][1]["lines"] = long_lines
    dense_layout = dashboard.weekday_layout(value)
    assert any(item["folded"] for item in dense_layout)
    assert sum(item["height"] for item in dense_layout) <= 309


def test_compaction_removes_redundant_optional_bring_verb():
    value = weekday_grid_state()
    value["hero"]["children"][0]["lines"] = ["Valgfrit: medbring hinkesten"]
    value["weekdays"][0]["children"][0]["lines"] = ["Valgfrit: tag hinkesten"]
    _compact_state(value)
    assert value["hero"]["children"][0]["lines"] == ["Valgfrit: hinkesten"]
    assert value["weekdays"][0]["children"][0]["lines"] == ["Valgfrit: hinkesten"]


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


def attention_state():
    value = weekday_grid_state()
    # A deadline today is time-urgent: the one thing that earns the yellow marker.
    value["hero"]["children"][0]["lines"] = ["Frist: aflever samtykke til turen"]
    value["hero"]["children"][0]["kinds"] = ["deadline"]
    value["weekdays"][0]["children"][1]["heading"] = "Afklar"
    value["weekdays"][0]["shared"] = "Fælles: forældremøde kl. 17"
    value["day_weather"] = [
        {"date": day["date"], "icon": icon, "low": 9, "high": 14, "rain_mm": 3}
        for day, icon in zip(value["weekdays"], ("rain", "sun", "partly", "cloud", "fog"))
    ]
    return value


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


def test_renderer_suppresses_child_heading_that_only_repeats_name():
    value = state()
    value["hero"]["children"][0]["heading"] = "ChildA"
    html = dashboard.render_html(value)
    assert '<h2>ChildA</h2>' in html
    assert '<strong class="category">ChildA</strong>' not in html
    assert dashboard.render_png(value).size == (800, 480)


def test_five_day_lookahead_requires_three_unfolded_and_two_folded_days():
    value = five_day_weekend_state()
    value["horizon"].pop()
    with pytest.raises(ValueError, match="three unfolded and two folded"):
        dashboard.validate(value)
    value = five_day_weekend_state()
    value["later"].append({"day": "Søn. 27.", "summary": "Tom"})
    with pytest.raises(ValueError, match="at most two folded"):
        dashboard.validate(value)


def test_stale_data_never_claims_current_inbox_count():
    value = state()
    now = datetime.now(timezone.utc)
    value["freshness"] = {"source_updated_at": (now-timedelta(days=3)).isoformat(),
                          "composed_at": now.isoformat(), "expires_at": (now-timedelta(days=1)).isoformat(),
                          "status": "fresh"}
    html = dashboard.render_html(value)
    assert "ÆLDRE / UFULDSTÆNDIGE DATA" in html
    assert "Beskeder ikke tjekket" in html
    assert "7 ulæste" not in html


def test_overflow_and_changed_child_order_are_rejected():
    value = state()
    value["hero"]["children"][0]["lines"] = ["x"] * 4
    with pytest.raises(ValueError):
        dashboard.validate(value)
    value = state()
    value["horizon"] = [{"day": "Tir", "children": list(reversed(copy.deepcopy(value["hero"]["children"])))}]
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


def test_composition_rejects_lost_fact_or_false_unread_total():
    value = state()
    now = datetime.now(timezone.utc)
    value["freshness"] = {"source_updated_at": now.isoformat(), "composed_at": now.isoformat(),
                          "expires_at": (now+timedelta(hours=36)).isoformat(), "status": "fresh"}
    packet = {"items": [{"ref": "fact-1"}], "freshness": value["freshness"],
              "messages": copy.deepcopy(value["messages"]), "children": ["ChildA", "ChildB"]}
    audit = {"source_updated_at": now.isoformat(), "facts": []}
    with pytest.raises(ValueError, match="exactly once"):
        composition.validate_composition(packet, value, audit)
    audit["facts"] = [{"ref": "fact-1", "decision": "omitted", "reason": "uncertain"}]
    composition.validate_composition(packet, value, audit)
    value["messages"]["unread_count"] = 1
    with pytest.raises(ValueError, match="aggregate counts"):
        composition.validate_composition(packet, value, audit)


def test_composition_rejects_child_name_repeated_as_heading():
    value = state()
    now = datetime.now(timezone.utc)
    value["freshness"] = {"source_updated_at": now.isoformat(), "composed_at": now.isoformat(),
                          "expires_at": (now + timedelta(hours=36)).isoformat(), "status": "fresh"}
    value["hero"]["children"][0]["heading"] = "ChildA"
    packet = {"items": [], "freshness": value["freshness"],
              "messages": copy.deepcopy(value["messages"]), "children": ["ChildA", "ChildB"]}
    audit = {"source_updated_at": now.isoformat(), "facts": []}
    with pytest.raises(ValueError, match="heading must describe status"):
        composition.validate_composition(packet, value, audit)


def test_composition_requires_semantic_heading_and_private_message_provenance():
    value = state()
    now = datetime.now(timezone.utc)
    value["freshness"] = {"source_updated_at": now.isoformat(), "composed_at": now.isoformat(),
                          "expires_at": (now + timedelta(hours=36)).isoformat(), "status": "fresh"}
    packet = {"items": [], "freshness": value["freshness"],
              "messages": copy.deepcopy(value["messages"]), "children": ["ChildA", "ChildB"]}
    audit = {"source_updated_at": now.isoformat(), "facts": []}
    value["hero"]["children"][0]["heading"] = "Husk mandag"
    with pytest.raises(ValueError, match="describe content rather than repeat date"):
        composition.validate_composition(packet, value, audit)

    value["hero"]["children"][0]["heading"] = "Weekendbesked"
    value["weekend"] = [{"day": "Lørdag 26. sep.", "text": "Evt. brætspil"}]
    packet["items"] = [{"ref": "fact-1", "type": "bring", "private_message_fact": True,
                        "actionability": "recommended"}]
    audit["facts"] = [{"ref": "fact-1", "decision": "displayed",
                       "location": "weekend[0].text", "reason": "selected"}]
    with pytest.raises(ValueError, match="show message provenance"):
        composition.validate_composition(packet, value, audit)
    value["weekend"][0]["text"] = "Besked: Brætspil"
    with pytest.raises(ValueError, match="remain visibly optional"):
        composition.validate_composition(packet, value, audit)
    value["weekend"][0]["text"] = "Besked: Valgfrit brætspil"
    composition.validate_composition(packet, value, audit)
    value["weekend"][0]["text"] = "Besked: aktivitet registreret; evt. bog"
    with pytest.raises(ValueError, match="without filler"):
        composition.validate_composition(packet, value, audit)


def test_composition_merges_private_event_with_retained_bring_detail():
    value = state()
    now = datetime.now(timezone.utc)
    value["freshness"] = {"source_updated_at": now.isoformat(), "composed_at": now.isoformat(),
                          "expires_at": (now + timedelta(hours=36)).isoformat(), "status": "fresh"}
    value["weekend"] = [{"day": "Lør. 26. sep.",
                          "text": "ChildA · Besked: Event; valgfrit brætspil"}]
    packet = {"items": [
        {"ref": "bring", "type": "bring", "child": "ChildA",
         "effective_date": "2026-09-26", "private_message_fact": True,
         "actionability": "recommended"},
        {"ref": "event", "type": "event", "child": "ChildA",
         "effective_date": "2026-09-26", "private_message_fact": True,
         "actionability": "informational"},
    ], "freshness": value["freshness"], "messages": copy.deepcopy(value["messages"]),
              "children": ["ChildA", "ChildB"]}
    audit = {"source_updated_at": now.isoformat(), "facts": [
        {"ref": "bring", "decision": "omitted", "reason": "row limit"},
        {"ref": "event", "decision": "displayed", "location": "weekend[0].text",
         "reason": "selected"},
    ]}
    with pytest.raises(ValueError, match="include its retained bring-item"):
        composition.validate_composition(packet, value, audit)
    audit["facts"][0] = {"ref": "bring", "decision": "merged",
                          "location": "weekend[0].text", "reason": "same message row"}
    composition.validate_composition(packet, value, audit)


@pytest.mark.parametrize(
    "fact,line,error,corrected",
    [
        ({"type": "bring", "actionability": "required"}, "Bevægeligt tøj",
         "state the action", "Medbring: bevægeligt tøj"),
        ({"type": "homework", "actionability": "required"}, "Dansk s. 52-53",
         "explicit action verb", "Læs dansk s. 52-53"),
    ],
)
def test_composition_requires_action_semantics(fact, line, error, corrected):
    value = state()
    now = datetime.now(timezone.utc)
    value["freshness"] = {"source_updated_at": now.isoformat(), "composed_at": now.isoformat(),
                          "expires_at": (now + timedelta(hours=36)).isoformat(), "status": "fresh"}
    value["hero"]["children"][0]["lines"] = [line]
    packet = {"items": [{"ref": "fact-1", **fact}], "freshness": value["freshness"],
              "messages": copy.deepcopy(value["messages"]), "children": ["ChildA", "ChildB"]}
    audit = {"source_updated_at": now.isoformat(),
             "facts": [{"ref": "fact-1", "decision": "displayed",
                        "location": "hero.children[0].lines[0]", "reason": "selected"}]}
    with pytest.raises(ValueError, match=error):
        composition.validate_composition(packet, value, audit)
    value["hero"]["children"][0]["lines"] = [corrected]
    composition.validate_composition(packet, value, audit)


def test_composition_rejects_fact_on_wrong_dashboard_date_and_early_footer():
    value = five_day_weekend_state()
    now = datetime.fromisoformat("2026-09-25T23:00:00+02:00")
    value["hero"]["day"] = "Mandag 28. sep."
    value["weekend"] = [
        {"day": "Lør. 26. sep.", "text": "Aktivitet"},
        {"day": "Søn. 27. sep.", "text": "Aktivitet"},
    ]
    for item, day in zip(value["horizon"], ("Tir. 29. sep.", "Ons. 30. sep.", "Tor. 1. okt.")):
        item["day"] = day
    value["later"] = [
        {"day": "Fre. 2. okt.", "summary": "Ingen punkter"},
        {"day": "Lør. 3. okt.", "summary": "Ingen punkter"},
    ]
    value["freshness"] = {"source_updated_at": now.isoformat(), "composed_at": now.isoformat(),
                          "expires_at": (now + timedelta(hours=36)).isoformat(), "status": "fresh"}
    packet = {"now": now.isoformat(),
              "items": [{"ref": "fact-1", "effective_date": "2026-10-04"}],
              "freshness": value["freshness"], "messages": copy.deepcopy(value["messages"]),
              "children": ["ChildA", "ChildB"]}
    audit = {"source_updated_at": now.isoformat(),
             "facts": [{"ref": "fact-1", "decision": "displayed",
                        "location": "weekend[1].text", "reason": "selected"}]}
    with pytest.raises(ValueError, match="fact date must match"):
        composition.validate_composition(packet, value, audit)

    packet["items"] = []
    audit["facts"] = []
    value["footer"] = "30. sep.: Frist"
    with pytest.raises(ValueError, match="footer must follow"):
        composition.validate_composition(packet, value, audit)


def test_v2_routes_closing_weekend_activity_to_fixed_weekend_area():
    value = weekday_grid_state("2026-09-28T08:00:00+02:00")
    value["hero"]["day"] = "Mandag 28. sep."
    for offset, label in enumerate(("Man. 28. sep.", "Tir. 29. sep.", "Ons. 30. sep.",
                                    "Tor. 1. okt.", "Fre. 2. okt.")):
        value["weekdays"][offset]["date"] = (
            f"2026-09-{28 + offset:02d}" if offset < 3 else f"2026-10-{offset - 2:02d}"
        )
        value["weekdays"][offset]["day"] = label
    value["weekend"] = [{"day": "Søn. 4. okt.", "text": "Begge · Svømmedag"}]
    packet = {
        "now": "2026-09-28T08:00:00+02:00",
        "items": [{"ref": "fact-1", "effective_date": "2026-10-04"}],
        "freshness": value["freshness"],
        "messages": copy.deepcopy(value["messages"]),
        "children": ["ChildA", "ChildB"],
    }
    audit = {"source_updated_at": value["freshness"]["source_updated_at"],
             "facts": [{"ref": "fact-1", "decision": "displayed",
                        "location": "weekend[0].text", "reason": "selected"}]}
    composition.validate_composition(packet, value, audit)

    value["weekend"] = []
    value["footer"] = "Søn. 4. okt.: Svømmedag"
    audit["facts"][0]["location"] = "footer"
    with pytest.raises(ValueError, match="fixed weekend area"):
        composition.validate_composition(packet, value, audit)


def test_composition_requires_substitute_name_subject_and_class_on_display():
    value = state()
    now = datetime.now(timezone.utc)
    value["freshness"] = {"source_updated_at": now.isoformat(), "composed_at": now.isoformat(),
                          "expires_at": (now + timedelta(hours=36)).isoformat(), "status": "fresh"}
    packet = {"items": [{"ref": "fact-1", "type": "substitute",
                          "title": "Alex Andersen er vikar i MAT · 2.A"}],
              "freshness": value["freshness"], "messages": copy.deepcopy(value["messages"]),
              "children": ["ChildA", "ChildB"]}
    audit = {"source_updated_at": now.isoformat(),
             "facts": [{"ref": "fact-1", "decision": "displayed",
                        "location": "hero.children[0].lines[0]", "reason": "selected"}]}
    value["hero"]["children"][0]["lines"] = ["Vikar i matematik"]
    with pytest.raises(ValueError, match="retain name and lesson/class"):
        composition.validate_composition(packet, value, audit)
    value["hero"]["children"][0]["lines"] = ["Vikar: Alex Andersen · MAT · 2.A"]
    composition.validate_composition(packet, value, audit)

    packet["items"][0]["title"] = "Alexander Wium-Ullerichs er vikar i KULTUR · 00"
    value["hero"]["children"][0]["lines"] = ["Vikar: Alexander W.-U. · KULTUR · 00"]
    composition.validate_composition(packet, value, audit)
    value["hero"]["children"][0]["lines"] = ["Vikar: Alex. W.-U. · KULTUR 00"]
    composition.validate_composition(packet, value, audit)

    packet["items"][0]["title"] = "Sanne Nørgaard Merkens er vikar i KULTUR · 00"
    value["hero"]["children"][0]["lines"] = ["Vikar: Sanne N. M. · KULTUR · 00"]
    composition.validate_composition(packet, value, audit)

    value["hero"]["children"][0]["lines"] = ["Vikar: Sanne N. M. · KULTUR"]
    with pytest.raises(ValueError, match="retain name and lesson/class"):
        composition.validate_composition(packet, value, audit)


@pytest.mark.parametrize("line, expected", [
    ("Vikar: Thorbjørn Z. G. · DAN 00", "Vikar Thorbjørn · DAN 00"),
    ("Vikar: Alexander W.-U. · KULTUR 00", "Vikar Alexander · KULTUR 00"),
    ("Vikar: Vitus A. A. M. · ENG 02", "Vikar Vitus · ENG 02"),
    ("Læs s. 54–55; lav s. 27", "Læs s. 54–55; lav s. 27"),
    ("Vikar i matematik", "Vikar i matematik"),
])
def test_substitute_lines_are_shortened_for_display_only(line, expected):
    assert dashboard.short_substitute(line) == expected
    value = weekday_grid_state()
    value["hero"]["children"][0]["lines"] = [line]
    dashboard.validate(value)  # the state keeps the full validated form
    assert expected in dashboard.render_html(value)
