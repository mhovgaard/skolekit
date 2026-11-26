"""The children's day view: when it shows, what it holds and how both renderers draw it."""
import copy
from datetime import date, datetime

import pytest

from skoleklar.runtime import scan as scan_service
from skoleklar.dashboard_timing import ZONE
from skoleklar.day_view import attach_day_view, build_day_view, clothing, day_lessons, view_date
from skolekit.records import SourceCheck, SourceRecord, calendar_source_key
from skoleklar.storage import SnapshotStore
from tests_python.test_dashboard_browser_layout import CHROME
from tests_python.board_lines import line
from tests_python.test_dashboard_rendering import dashboard, weekday_grid_state


def local(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZONE)


def lesson(start, end, code, day="2026-09-21", **fields):
    return {"start": f"{day}T{start}:00.0000000", "end": f"{day}T{end}:00.0000000",
            "lessonName": code, "lessonClassName": "00", "staffName": "Lærer L.", "title": code, **fields}


@pytest.mark.parametrize(("moment", "expected"), [
    ("2026-09-21T00:00", None),  # The night is the parents' board.
    ("2026-09-21T05:59", None),
    ("2026-09-21T06:00", "2026-09-21"),
    ("2026-09-21T08:59", "2026-09-21"),
    ("2026-09-21T09:00", None),
    ("2026-09-21T14:59", None),
    ("2026-09-21T15:00", "2026-09-22"),
    ("2026-09-21T19:59", "2026-09-22"),
    ("2026-09-21T20:00", None),
    ("2026-09-24T16:00", "2026-09-25"),
    ("2026-09-25T16:00", None),  # Friday afternoon belongs to the weekend panel.
    ("2026-09-26T07:00", None),
    ("2026-09-27T07:00", None),
    ("2026-09-27T16:00", "2026-09-28"),  # Sunday afternoon prepares Monday.
])
def test_view_shows_school_mornings_and_the_afternoon_before_a_school_day(moment, expected):
    result = view_date(local(moment))
    assert (result.isoformat() if result else None) == expected


def test_parallel_and_repeated_lessons_become_one_block_per_subject():
    blocks = day_lessons([
        lesson("09:00", "09:45", "DAN"), lesson("09:00", "09:45", "STØTTE"),
        lesson("10:00", "10:45", "DAN"), lesson("10:00", "10:45", "DAN"),
        lesson("10:45", "11:30", "MAT"),
        lesson("12:15", "13:00", "MAT"),  # After lunch: a block of its own.
        lesson("13:00", "13:45", "XYZ"),  # An unknown code is shown as given.
    ])
    assert [(b["start"], b["end"], b["subject"].replace("­", "")) for b in blocks] == [
        ("09:00", "10:45", "Dansk"), ("10:45", "11:30", "Matematik"),
        ("12:15", "13:00", "Matematik"), ("13:00", "13:45", "XYZ")]


def test_substitutes_and_cancellations_are_marked_but_the_own_teacher_is_not():
    blocks = day_lessons([
        lesson("09:00", "09:45", "DAN", substituteStaff="Lærer L."),
        lesson("10:00", "10:45", "ENG", substituteStaff="Mette Hansen"),
        lesson("10:45", "11:30", "ENG", substituteStaff="Mette Hansen"),
        lesson("12:15", "13:00", "MUS", title="MUS aflyst"),
    ])
    assert [(b["start"], b["end"], b["subject"], b["change"], b["note"]) for b in blocks] == [
        ("09:00", "09:45", "Dansk", None, ""), ("10:00", "11:30", "Engelsk", "vikar", "Mette"),
        ("12:15", "13:00", "Musik", "aflyst", "")]


def test_both_rows_share_the_bell_schedule_and_a_day_without_lessons_has_no_view():
    view = build_day_view(date(2026, 9, 21), ["ChildA", "ChildB"], "I dag", {
        "ChildA": [lesson("09:00", "09:45", "DAN"), lesson("10:00", "10:45", "DAN")],
        "ChildB": [lesson("09:00", "09:45", "MAT"), lesson("10:00", "10:45", "IDR")]})
    assert view["slots"] == ["09:00", "10:00"]
    assert [len(child["lessons"]) for child in view["children"]] == [1, 2]
    assert build_day_view(date(2026, 9, 21), ["ChildA", "ChildB"], "I dag", {}) is None


@pytest.mark.parametrize(("weather", "expected"), [
    (None, ""),
    ({"icon": "rain", "low": 4, "high": 9, "rain_mm": 3}, "Jakke, hue og regntøj"),
    ({"icon": "snow", "low": -2, "high": 1, "rain_mm": 0}, "Vinterjakke, hue og vanter"),
    ({"icon": "sun", "low": 14, "high": 22, "rain_mm": 0}, "T-shirt"),
])
def test_clothes_follow_the_school_day_weather(weather, expected):
    assert clothing(weather) == expected


def view_state(composed="2026-09-21T07:00:00+02:00"):
    state = weekday_grid_state(composed)
    day = state["hero"]["date"]
    state["hero"]["children"][0]["lines"] = [line("idrætstøj", "bring", day=day),
                                             line("Mette H. · DAN 1.B", "substitute", day=day)]
    state["hero"]["children"][1]["lines"] = [line("idrætstøj", "bring", day=day),
                                             line("læs s. 4", "homework", day=day)]
    return state


def lookup(day):
    return {"ChildA": [lesson("09:00", "09:45", "DAN", day.isoformat())],
            "ChildB": [lesson("09:00", "09:45", "MAT", day.isoformat(), substituteStaff="Mette Hansen"),
                       lesson("10:00", "10:45", "KRI", day.isoformat())]}


def test_view_is_attached_only_in_its_window_and_for_the_hero_day():
    state = attach_day_view(view_state(), lookup)
    assert state["day_view"]["date"] == "2026-09-21" and state["day_view"]["label"] == "I dag"
    dashboard.validate(state)
    assert "day_view" not in attach_day_view(view_state("2026-09-21T10:00:00+02:00"), lookup)
    assert "day_view" not in attach_day_view(view_state(), lambda day: {})
    # At 15:00 the view shows Tuesday; a hero still on Monday would list the wrong things.
    still_monday = view_state("2026-09-21T16:00:00+02:00")
    still_monday["hero"].update(day="Mandag 21. sep.", date="2026-09-21")
    assert "day_view" not in attach_day_view(still_monday, lookup)
    tuesday = view_state("2026-09-21T16:00:00+02:00")
    assert tuesday["hero"]["date"] == "2026-09-22"
    assert attach_day_view(tuesday, lookup)["day_view"]["label"] == "I morgen"


def rendered_state():
    state = attach_day_view(view_state(), lookup)
    state["day_weather"] = [{"date": "2026-09-21", "icon": "rain", "low": 4, "high": 9, "rain_mm": 3}]
    state["day_summaries"] = [{"date": day["date"], "children": [
        {"name": "ChildA", "status": "ok", "headline": "Bogstavet E",
         "text": "I dansk møder du edderkoppen Egon og skriver E. " * 4},
        {"name": "ChildB", "status": "not_published", "headline": None, "text": None}]}
        for day in state["weekdays"]]
    return state


def test_both_renderers_draw_the_view():
    state = rendered_state()
    html = dashboard.render_html(state)
    assert 'class="dashboard day-view"' in html and "Vikar Mette" in html
    assert "Fri kl. 9.45" in html and "Fri kl. 10.45" in html
    assert "<b>Tøj</b> · Jakke, hue og regntøj" in html
    assert "<b>Begge</b> · Idrætstøj" in html and "<b>ChildB</b> · Lektie: læs s. 4" in html
    # The substitute is on the lesson tile, not in the remember list.
    assert "Vikar Mette · DAN" not in html
    assert "Bogstavet E" in html and "punkter at tjekke" not in html
    image = dashboard.render_png(state)
    dashboard.validate_panel_image(image)
    assert len(dashboard.pack_panel_frame(image)) == 192_000


@pytest.mark.parametrize("breaks", [
    lambda view: view.update(label="Snart"),
    lambda view: view.update(extra=1),
    lambda view: view["children"].reverse(),
    lambda view: view["children"][0]["lessons"][0].update(note="Mette"),
    lambda view: view["children"][0]["lessons"][0].update(start="08:00"),
    lambda view: view["children"][0]["lessons"][0].update(subject="x" * 40),
    lambda view: view["children"][0]["lessons"][0].update(change="flyttet"),
    lambda view: view.update(date="2026-09-26"),
])
def test_view_fields_are_allowlisted(breaks):
    state = rendered_state()
    breaks(state["day_view"])
    with pytest.raises(ValueError):
        dashboard.validate(state)


def store_lessons(store, raws):
    run = store.begin_run()
    records = [SourceRecord(
        student_id=1, child="ChildA", source_type="calendar",
        source_key=calendar_source_key("LessonsEvents", raw), source_url="https://example.invalid/cal",
        title=raw["title"], dates=[{"kind": "event", "start": raw["start"], "end": raw["end"], "all_day": False}],
        source_data={"calendar_kind": "LessonsEvents", "event": raw},
        content_data={"lessonName": raw["lessonName"]}, state_data={"calendar_kind": "LessonsEvents"},
        raw_payload=raw) for raw in raws]
    store.store_checks(run, [SourceCheck(
        student_id=1, child="ChildA", source_type="calendar", records=records,
        coverage={"kind": "date_window", "start": "2026-09-14", "end": "2026-10-05"})])


def test_cloud_extras_read_the_stored_lessons_and_can_be_switched_off(tmp_path, monkeypatch):
    with SnapshotStore(tmp_path / "school.db") as store:
        store_lessons(store, [lesson("09:00", "09:45", "DAN"),
                              lesson("09:00", "09:45", "MAT", day="2026-09-22")])
        assert [(child, event["lessonName"]) for child, event in store.lesson_slots("2026-09-21")] == [
            ("ChildA", "DAN")]
        state = scan_service._frame_extras(store)(view_state())
        assert state["day_view"]["children"][0]["lessons"][0]["subject"] == "Dansk"
        assert state["day_summaries"][0]["children"][0]["status"] == "not_published"
        monkeypatch.setenv("AG_DAY_VIEW", "off")
        assert "day_view" not in scan_service._frame_extras(store)(view_state())


def measure(html, width):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": width, "height": 1200})
        page.set_content(html, wait_until="load")
        page.evaluate("document.fonts.ready")
        result = page.evaluate("""() => {
            const panel=document.querySelector('.dashboard'),r=e=>e.getBoundingClientRect();
            const rows=[...document.querySelectorAll('.lessons')].map(row=>
                [...row.children].map(tile=>Math.round(r(tile).left)));
            return {
                clipped:[...panel.querySelectorAll('*')].filter(e=>{
                    const s=getComputedStyle(e);
                    return s.display!=='contents'&&s.display!=='inline'&&(
                        (e.clientWidth>0&&e.scrollWidth>e.clientWidth+1)||
                        (e.clientHeight>0&&e.scrollHeight>e.clientHeight+1));
                }).map(e=>e.tagName+'.'+e.className+':'+e.textContent.slice(0,20)),
                rows, panel:[r(panel).width,r(panel).height],
                documentWidth:document.documentElement.scrollWidth, viewport:innerWidth,
            };
        }""")
        browser.close()
    return result


@pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not available")
def test_an_ordinary_day_keeps_the_panel_shape():
    state = rendered_state()
    # Fresh data: the old-data warning may add a line, and the panel then grows.
    state["freshness"]["expires_at"] = "2099-01-01T00:00:00+00:00"
    result = measure(dashboard.render_html(state), 1000)
    assert result["clipped"] == []
    assert round(result["panel"][0] * 3 / 5) == round(result["panel"][1])


@pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not available")
@pytest.mark.parametrize("width", [1000, 390])
def test_a_full_day_is_never_clipped_and_both_rows_share_the_slot_columns(width):
    state = rendered_state()
    long_day = {"ChildA": [lesson(start, end, code) for start, end, code in (
                    ("08:15", "09:00", "MOR"), ("09:00", "09:45", "DAN"), ("10:00", "10:45", "N/T"),
                    ("10:45", "11:30", "BIL"), ("12:15", "13:00", "KRI"), ("13:00", "13:45", "HDS"))],
                "ChildB": [lesson(start, end, code, substituteStaff="Annemette Kristiansen")
                           for start, end, code in (("08:15", "09:00", "MAT"), ("09:00", "09:45", "ENG"),
                                                    ("10:00", "10:45", "IDR"), ("10:45", "11:30", "MUS"),
                                                    ("12:15", "13:00", "SAM"), ("13:00", "13:45", "MAD"),
                                                    ("13:45", "14:30", "HIS"))]}
    state["day_view"] = build_day_view(date(2026, 9, 21), ["ChildA", "ChildB"], "I dag", long_day)
    result = measure(dashboard.render_html(state), width)
    assert result["clipped"] == []
    assert result["documentWidth"] <= result["viewport"]
    if width == 1000:
        # The same slots start at the same x in both rows.
        assert result["rows"][0][:2] == result["rows"][1][:2]


def test_the_panel_keeps_long_days_inside_their_tiles():
    from PIL import Image, ImageDraw
    from skoleklar.day_view_render import column_edges

    state = rendered_state()
    many = {"ChildA": [lesson(f"{8 + i}:00".zfill(5), f"{8 + i}:45".zfill(5), code)
                       for i, code in enumerate(("HDS", "KRI", "N/T", "MAD", "SAM", "BIL", "FYS"))],
            "ChildB": [lesson("08:00", "08:45", "DAN", substituteStaff="Annemette Kristiansen")]}
    view = build_day_view(date(2026, 9, 21), ["ChildA", "ChildB"], "I dag", many)
    edges = column_edges(ImageDraw.Draw(Image.new("RGB", (800, 480))), view)
    assert edges[0][0] == 354 and edges[-1][1] == 790  # the board's narrow edges
    assert all(right > left for left, right in edges)
    assert all(edges[i][1] < edges[i + 1][0] for i in range(len(edges) - 1))
    state["day_view"] = copy.deepcopy(view)
    dashboard.validate_panel_image(dashboard.render_png(state))


def test_the_childs_own_part_leads_the_summary_and_is_never_cut(monkeypatch):
    """A day-summary-v2 text puts the child's own group first; the panel cuts only its end."""
    from skoleklar import day_view_render

    state = rendered_state()
    own = "Du og Emma laver en bogstavhistorie med bogstaverne F og H."
    long_text = own + " Først læser du i din egen bog. I engelsk synger I en sang om dyr og tæller til " \
        "tyve, og i matematik bygger I figurer af tændstikker og tæller, hvor mange I har brugt. " \
        "Husk: læs side 12 og 13 i læsebogen."
    assert 250 <= len(long_text) <= 480
    for entry in state["day_summaries"]:
        entry["children"][1].update(status="ok", headline="Bogstavhistorie med Emma: F og H", text=long_text)
    dashboard.validate(state)
    drawn = []
    original = day_view_render.board.draw_text
    monkeypatch.setattr(day_view_render.board, "draw_text",
                        lambda draw, xy, text, *rest, **kw: drawn.append(text) or original(draw, xy, text, *rest, **kw))
    dashboard.render_png(state)
    joined = " ".join(drawn)
    assert "Bogstavhistorie med Emma: F og H" in drawn
    assert own in joined  # The first sentence is drawn whole, whatever is cut after it.
    assert own in dashboard.render_html(state)


def test_the_remember_list_reads_each_lines_kind():
    """'(hvis du vil)', 'Lektie:' and what is left out come from the kinds, not the words."""
    from skoleklar.day_view_render import remember_items

    state = rendered_state()
    child = state["hero"]["children"][0]
    # The repeated line is dropped, and each line reads as its kind says.
    child["lines"] = [line("idrætstøj", "bring"), line("hinkesten", "optional"), line("Mette · DAN", "substitute"),
                      line("Læs s. 4", "homework"), line("Fødselsdagsfest", "party", cancelled=True)]
    state["hero"]["children"][1].update(lines=[line("idrætstøj", "bring")])
    for day in state["weekdays"]:
        day.update(shared=[line("Morgensang", "routine")])
    dashboard.validate(state)
    items = [what for who, what in remember_items(state) if who != "Tøj"]
    assert items == ["Idrætstøj", "Hinkesten (hvis du vil)", "Lektie: Læs s. 4", "Fødselsdagsfest er aflyst"]
    child["lines"] = [line("s. 4", "homework", school_subject="Matematik")]
    state["hero"]["children"][1].update(lines=[])
    assert [what for who, what in remember_items(state) if who != "Tøj"] == ["Lektie: Mat. s. 4"]
