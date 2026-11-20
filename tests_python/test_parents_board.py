"""The parents' board: line kinds, the days ahead, later events, urgency, the drop
order and the message topics. Synthetic data only; no school data or network."""
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from skoleklar import dashboard_renderer as dashboard
from skoleklar import fact as fact_model
from skoleklar import parents_board as pb
from skoleklar import parents_board_render as render
from skoleklar.live_subjects import DisplayMessage, with_topics
from skoleklar.triage.message_topics import topic_writer, topics_mode, valid_topic
from tests_python.board_lines import line

# This refactor's core set: `pytest -m pipeline` (docs/maintenance.md).
pytestmark = pytest.mark.pipeline

NAMES = ["Alfa", "Beta"]


class _SundayEvening(datetime):
    """The clock when the heavy week was composed, so its freshness never runs out."""

    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat("2026-11-08T19:30:00+01:00").astimezone(tz)


@pytest.fixture(autouse=True)
def _frozen_board_clock(monkeypatch):
    monkeypatch.setattr(dashboard, "datetime", _SundayEvening)


def hw(text, subject, iso):
    return line(text, "homework", day=iso, school_subject=subject)


def child(name, lines):
    return {"name": name, "lines": lines}


def day(iso, label, alfa, beta):
    return {"date": iso, "day": label, "children": [child("Alfa", alfa), child("Beta", beta)], "shared": []}


def heavy_week_state():
    """Sunday 8 November 2026, 19.30: homework in two subjects every day but Thursday's trips."""
    mon, tue, wed, thu, fri = (f"2026-11-{value:02d}" for value in range(9, 14))
    return {
        "schema_version": 3, "week_label": "UGE 46", "updated_label": "DATA SØN 8. NOV. · 19.30",
        "freshness": {"source_updated_at": "2026-11-08T19:30:00+01:00",
                      "composed_at": "2026-11-08T19:30:00+01:00",
                      "expires_at": "2026-11-10T07:30:00+01:00", "status": "fresh"},
        "hero": {"state": "Næste skoledag", "day": "Mandag 9. nov.", "date": mon, "children": [
            child("Alfa", [hw("læs 15 min i læsebogen", "Dansk", mon), hw("arbejdsbog s. 24", "Matematik", mon),
                           line("aflever samtykke til turen", "deadline", day=mon),
                           line("idrætstøj", "bring", day=mon)]),
            child("Beta", [hw("ark med 7-tabellen", "Matematik", mon), hw("læs s. 88–90", "Dansk", mon),
                           line("Skolehjem 15.00–17.00", day=mon)])]},
        "weekdays": [
            day(mon, "Man. 9. nov.",
                [hw("læs 15 min i læsebogen", "Dansk", mon), hw("arbejdsbog s. 24", "Matematik", mon)],
                [hw("ark med 7-tabellen", "Matematik", mon), hw("læs s. 88–90", "Dansk", mon)]),
            day(tue, "Tir. 10. nov.",
                [hw("øv ugens 5 stavord", "Dansk", tue), hw("lær farverne", "Engelsk", tue)],
                [hw("10 gloser", "Engelsk", tue), hw("skriv 5 sætninger om en fabel", "Dansk", tue),
                 line("badetøj", "bring", day=tue)]),
            day(wed, "Ons. 11. nov.",
                [hw("s. 25–26", "Matematik", wed), hw("tegn et efterårstræ", "N/T", wed)],
                [hw("s. 46–47", "Matematik", wed), hw("svar på spørgsmål s. 12", "N/T", wed)]),
            day(thu, "Tor. 12. nov.",
                [line("Kulturdag på kunstmuseet, hjem ca. 14", "trip", day=thu),
                 line("Mødetid 7.45 ved bussen", "change", day=thu),
                 line("madpakke, drikkedunk og regntøj", "bring", day=thu, rain_gear=True)],
                [line("Kulturdag i teatret", "trip", day=thu), line("madpakke", "bring", day=thu)]),
            day(fri, "Fre. 13. nov.",
                [hw("læs 15 min", "Dansk", fri), hw("s. 27", "Matematik", fri)],
                [hw("s. 48", "Matematik", fri), hw("læs teksten s. 14", "Engelsk", fri),
                 line("idrætstøj", "bring", day=fri)]),
        ],
        "weekend": [], "weekend_overflow": 0,
        "messages": {"checked": True, "bodies_retained": 0, "unread_count": 4, "items": []},
        "day_weather": [{"date": "2026-11-09", "icon": "cloud", "low": 1, "high": 5, "rain_mm": 0},
                        {"date": "2026-11-12", "icon": "rain", "low": 7, "high": 9, "rain_mm": 3}],
        "day_summaries": [{"date": "2026-11-09", "children": [
            {"name": "Alfa", "status": "ok", "headline": "Bogstavet F og tal til 20", "text": "Tekst."},
            {"name": "Beta", "status": "ok", "headline": "7-tabellen og fabler", "text": "Tekst."}]}],
        "later_events": [
            {"date": "2026-11-16", "who": "Begge", "line": line("tilmeld skolefest", "deadline", day="2026-11-16")},
            {"date": "2026-11-19", "who": "Begge", "line": line("Skolefest 17.00–19.30", "party", day="2026-11-19")},
            {"date": "2026-11-20", "who": "Beta", "line": line("Forældresamtale 16.40", "meeting", day="2026-11-20")},
            {"date": "2026-11-23", "who": "Alfa", "line": line("Forældremøde 19.00", "meeting", day="2026-11-23")}],
        "device": {"battery_percent": 82},
    }


def live_messages():
    return [DisplayMessage("Alfa", "Karen H.", "Husk samtykke til turen", "safe", "08/11 17:20", True,
                           "Samtykke tur"),
            DisplayMessage("Beta", "Simon B.", "Lusetjek i 3.b", "safe", "06/11 14:05", False, "Lusetjek")]


def drawn_texts(monkeypatch, state, messages=None):
    texts = []
    original = render._text
    monkeypatch.setattr(render, "_text", lambda draw, x, y, text, *rest: texts.append(text)
                        or original(draw, x, y, text, *rest))
    image = dashboard.render_png(state, live_messages=messages)
    return image, " ".join(texts)


def weekend_row(iso, label, who, *lines):
    return {"date": iso, "day": label, "who": who, "lines": list(lines)}


# --- What each line is -------------------------------------------------------------------

@pytest.mark.parametrize("fact, kind", [
    # The extractor says what a fact is (skoleklar.fact); the title is never read.
    ({"type": "homework"}, "homework"),
    ({"type": "bring", "actionability": "required"}, "bring"),
    ({"type": "bring", "actionability": "recommended"}, "optional"),
    ({"type": "parent_action", "actionability": "required", "title": "Tilmeld skolefest"}, "deadline"),
    ({"type": "parent_action", "title": "Book en samtale"}, "deadline"),
    ({"type": "parent_action", "title": "Kom til informationsaften", "category": "meeting"}, "meeting"),
    ({"type": "event", "title": "Morgensang i salen", "recurring": True, "category": "other"}, "routine"),
    ({"type": "event", "title": "Morgensang i salen", "recurring": True, "cancelled": True}, "event"),
    ({"type": "event", "title": "Skovdag", "category": "trip"}, "trip"),
    ({"type": "event", "title": "Samtaler", "category": "meeting"}, "meeting"),
    ({"type": "event", "title": "Turnering i hallen", "category": "other"}, "event"),
    ({"type": "event", "title": "Ida har fødselsdag", "category": "birthday"}, "info"),
    ({"type": "event", "activity_kind": "class_trip", "title": "Klassetur"}, "trip"),
    ({"type": "event", "activity_kind": "birthday_party", "title": "Claras fødselsdag"}, "party"),
    ({"type": "event", "activity_kind": "class_event", "confirmed_activity_kind": "class_trip"}, "trip"),
    # A fact that says nothing more is plain: its words make no trip or meeting of it.
    ({"type": "event", "title": "Skovtur"}, "event"),
    ({"type": "parent_action", "title": "Forældresamtale kl. 16.40"}, "deadline"),
    ({"type": "schedule_change", "title": "Fri kl. 12"}, "change"),
    ({"type": "schedule_change", "title": "Ny tid for svømning", "moves_time": True}, "time_change"),
    ({"type": "schedule_change", "title": "Idræt kl. 10 flyttes til salen", "moves_time": False}, "change"),
    ({"type": "substitute", "title": "Mette er vikar"}, "substitute"),
    ({"type": "context", "title": "Ny skolebestyrelse"}, "info"),
])
def test_a_facts_own_fields_decide_its_kind(fact, kind):
    assert fact_model.kind(fact) == kind


def test_a_line_that_joins_facts_is_its_most_important_kind():
    # One line that joins a trip and its packing list is the trip.
    assert fact_model.line_kind([{"type": "bring"}, {"type": "event", "category": "trip"}]) == "trip"
    assert fact_model.line_kind([{"type": "bring"}, {"type": "homework"}]) == "homework"
    # A routine joined with anything else is never hidden.
    assert fact_model.line_kind([{"type": "event", "recurring": True}, {"type": "context"}]) == "info"


def test_an_item_draws_what_its_line_says_never_its_words():
    item = render.make_item(line("Skolehjemssamtaler", "event", day="2026-11-09"), None)
    assert item.chip == "ARR." and item.runs == [("Skolehjemssamtaler", False)]
    item = render.make_item(line("Fødselsdag hos Dagny", "trip", day="2026-11-09"), None)
    assert item.chip == "TUR"
    item = render.make_item(line("s. 25–26", "homework", day="2026-11-09", school_subject="Matematik"), None)
    assert item.box and item.runs == [("Mat.", True), (" s. 25–26", False)]
    # The words are content only: a label in them is drawn as the text it is.
    item = render.make_item(line("Husk idrætstøj", "bring", day="2026-11-09"), None)
    assert item.runs == [("Medbring", True), (" Husk idrætstøj", False)]
    item = render.make_item(line("Medbring: x", day="2026-11-09"), None)
    assert item.runs == [("Medbring: x", False)]


@pytest.mark.parametrize("text, kind, extra, expected", [
    ("idrætstøj", "bring", {}, [("Medbring", True), (" idrætstøj", False)]),
    ("hinkesten", "optional", {}, [("Valgfrit", True), (" hinkesten", False)]),
    ("svar på invitationen", "deadline", {}, [("Frist", True), (" svar på invitationen", False)]),
    ("Alberte · DAN 1", "substitute", {}, [("Vikar", True), (" Alberte · DAN 1", False)]),
    ("Fødselsdagsfest", "party", {"cancelled": True}, [("Aflyst", True), (" Fødselsdagsfest", False)]),
    ("læs s. 4", "homework", {"school_subject": "Natur/teknologi"}, [("N/T", True), (" læs s. 4", False)]),
    ("læs s. 4", "homework", {}, [("Læs s. 4", False)]),
])
def test_a_lines_label_comes_from_its_facts(text, kind, extra, expected):
    entry = line(text, kind, day="2026-11-10", **extra)
    assert render.make_item(entry, date(2026, 11, 8)).runs == expected


# --- Days ahead and later events ------------------------------------------------------------

def context_at(now, items):
    return {"now": now, "items": items}


def fact(ref, child, day, kind="homework", title="Læs s. 4", **extra):
    return {"ref": ref, "child": child, "effective_date": day, "type": kind, "title": title,
            "actionability": "required", **extra}


def shown(lines):
    return [(entry["text"], entry["kind"], entry["label"]) for entry in lines]


def later(entries):
    return [(entry["date"], entry["who"], entry["line"]["kind"], entry["line"]["label"], entry["line"]["text"])
            for entry in entries]


def test_thursday_evening_looks_into_the_weekend_and_next_week():
    state = heavy_week_state()  # composed for 9–13 November
    context = context_at("2026-11-12T19:30:00+01:00", [
        fact("fact-1", "Alfa", "2026-11-14", "event", "Fødselsdagsfest", activity_kind="birthday_party"),
        fact("fact-2", "Beta", "2026-11-16", "homework", "Læs s. 90"),
        fact("fact-3", "Beta", "2026-11-17", "bring", "Medbring badetøj", bring_items=["badetøj"]),
        fact("fact-4", "Alfa", "2026-11-13", "homework", "Already in the week"),
        fact("fact-5", "Beta", "2026-11-20", "homework", "After the look-ahead"),
        fact("fact-6", "Alfa", "2026-11-16", "homework", "Uncertain", needs_review=True),
        fact("fact-7", "Elsewhere", "2026-11-16", "event", "Skolefest", category="party"),
        fact("fact-8", "Alfa", "2026-11-19", "homework", "Thursday is the fourth school day"),
    ])
    rows = pb.ahead_rows(context, state)
    # The next school day is Friday: Saturday has a party, Sunday nothing, then Monday to Thursday.
    assert [row["date"] for row in rows] == ["2026-11-14", "2026-11-16", "2026-11-17", "2026-11-19"]
    saturday, monday, tuesday, thursday = rows
    assert shown(saturday["children"][0]["lines"]) == [("Fødselsdagsfest", "party", "")]
    assert shown(monday["children"][1]["lines"]) == [("Læs s. 90", "homework", "")]
    assert monday["children"][0]["lines"] == []
    assert shown(monday["shared"]) == [("Skolefest", "party", "")]
    assert shown(tuesday["children"][1]["lines"]) == [("badetøj", "bring", "Medbring")]
    assert shown(thursday["children"][0]["lines"]) == [("Thursday is the fourth school day", "homework", "")]
    dashboard.validate({**state, "freshness": {**state["freshness"], "composed_at": "2026-11-12T19:30:00+01:00"},
                        "ahead": rows, "later_events": []})


def test_later_events_are_trips_parties_meetings_and_deadlines_two_weeks_out():
    state = heavy_week_state()
    context = context_at("2026-11-08T19:30:00+01:00", [
        fact("fact-1", "Alfa", "2026-11-12", "event", "Inside the look-ahead"),
        fact("fact-2", "Alfa", "2026-11-19", "event", "Skolefest", category="party"),
        fact("fact-3", "Beta", "2026-11-19", "event", "Skolefest", category="party"),
        fact("fact-4", "Beta", "2026-11-20", "parent_action", "Forældresamtale 16.40", category="meeting"),
        fact("fact-5", "Alfa", "2026-11-16", "parent_action", "Tilmeld skolefest"),
        fact("fact-6", "Alfa", "2026-11-18", "homework", "Homework is not an event"),
        fact("fact-7", "Alfa", "2026-11-24", "event", "Too far out"),
        fact("fact-8", "Beta", "2026-11-23", "event", "Juleklip", category="party", cancelled=True),
    ])
    assert later(pb.later_events(context, state)) == [
        ("2026-11-16", "Alfa", "deadline", "Frist", "Tilmeld skolefest"),
        ("2026-11-19", "Begge", "party", "", "Skolefest"),
        ("2026-11-20", "Beta", "meeting", "", "Forældresamtale 16.40"),
        ("2026-11-23", "Beta", "party", "Aflyst", "Juleklip"),
    ]


def test_morning_singing_is_a_routine_and_never_shown_unless_cancelled():
    state = heavy_week_state()
    state["weekdays"][4]["shared"] = [line("Morgensang", "routine", day="2026-11-13")]
    assert "Morgensang" not in dashboard.render_html(state)
    state["weekdays"][4]["shared"] = [line("Morgensang aflyst", "change", day="2026-11-13")]
    assert "Morgensang aflyst" in dashboard.render_html(state)


def test_a_classmates_birth_date_never_reaches_the_board():
    state = heavy_week_state()
    notice = fact("fact-1", "Alfa", "2026-11-19", "event", "Ida har fødselsdag", actionability="informational",
                  category="birthday")
    assert pb.later_events(context_at("2026-11-08T19:30:00+01:00", [notice]), state) == []


def test_yellow_follows_the_facts_kind_and_the_frames_date():
    today = date(2026, 11, 8)
    assert render.make_item(line("Ny mødetid", "time_change", day="2026-11-09"), today).urgent
    assert render.make_item(line("Ny mødetid", "time_change", day="2026-11-10"), today).urgent
    assert not render.make_item(line("Ny mødetid", "time_change", day="2026-11-11"), today).urgent
    assert not render.make_item(line("Idræt kl. 10 i salen", "change", day="2026-11-09"), today).urgent
    assert not render.make_item(line("Fødselsdagsfest", "deadline", day="2026-11-09", cancelled=True),
                                today).urgent


@pytest.mark.parametrize("change", [
    {"later_events": [{"date": "2026-11-19", "who": "Somebody", "line": line("Fest", "party")}]},
    {"later_events": [{"date": "2026-11-19", "who": "Begge", "line": line("Læs", "homework")}]},
    {"later_events": [{"date": "2026-11-19", "who": "Begge", "kind": "party", "text": "Fest"}]},
    {"ahead": [{"date": "2026-11-14", "children": [], "shared": []}]},
    {"ahead": [{"date": f"2026-11-{14 + n}", "shared": [], "children": [
        {"name": "Alfa", "lines": []}, {"name": "Beta", "lines": []}]}
        for n in range(pb.AHEAD_ROWS + 1)]},
    {"ahead": [{"date": "2026-11-14", "shared": [], "children": [
        {"name": "Alfa", "lines": ["Læs s. 4"]}, {"name": "Beta", "lines": []}]}]},
    {"device": {"battery_percent": 101}},
    {"device": {"battery_percent": 50, "serial": "x"}},
])
def test_parents_board_fields_are_allowlisted(change):
    with pytest.raises(ValueError):
        dashboard.validate({**heavy_week_state(), **change})


# --- The board on the panel ----------------------------------------------------------------

def test_heavy_week_shows_all_sixteen_homework_items_uncut(monkeypatch):
    state = heavy_week_state()
    dashboard.validate(state)
    model, heights = render.layout(state, live_messages())
    homework = [item for kid in model.kids for item in kid.left if item.kind == "homework"]
    homework += [item for kid in model.kids for _, items, _ in kid.days for item in items
                 if item.kind == "homework"]
    assert len(homework) == 16
    assert sum(heights) == 480 - render.HEADER_HEIGHT
    image, texts = drawn_texts(monkeypatch, state, live_messages())
    assert image.size == (800, 480) and image.mode == "P"
    assert "…" not in texts
    for expected in ("Mandag 9. nov.", "1–5° Vintertøj", "Bogstavet F og tal til 20", "Karen: Samtykke tur",
                     "08/11", "Simon: Lusetjek", "TUR", "Opdateret 08/11 19.30", "82 %", "4 ulæste i alt"):
        assert expected in texts, expected


def with_plans(state, composed, headlines=None):
    """`state` composed at `composed`, with a plan headline for each child on every shown school day."""
    from skoleklar.dashboard_timing import dashboard_focus

    state = {**state, "freshness": {**state["freshness"], "composed_at": composed}, "later_events": []}
    # The frame's hero is its next school day, as the timeline slices it.
    focus = dashboard_focus(datetime.fromisoformat(composed)).school_date
    card = next(entry for entry in state["weekdays"] if entry["date"] == focus.isoformat())
    state["hero"] = {**state["hero"], "date": focus.isoformat(), "children": card["children"]}
    dates = sorted({day["date"] for day in state["weekdays"]}
                   | {value.isoformat() for value in pb.lookahead_dates(focus)})
    headlines = headlines or {}
    state["day_summaries"] = [{"date": iso, "children": [
        {"name": name, "status": "ok", "headline": headlines.get((name, iso), f"{name} plan {iso[-2:]}"),
         "text": "Tekst."} for name in NAMES]} for iso in dates]
    return state


def day_lines(kid):
    return {iso: ["".join(text for text, _ in item.runs) for item in items] for iso, items, _ in kid.days}


def test_a_plan_line_is_left_out_when_a_line_already_says_it():
    state = heavy_week_state()
    for entry in state["weekdays"]:
        if entry["date"] != "2026-11-12":
            for kid in entry["children"]:
                kid.update(lines=[])
    # The summary for Beta's Thursday retells its trip (day-summary-v3); Alfa's does not.
    thursday = next(entry for entry in state["weekdays"] if entry["date"] == "2026-11-12")
    thursday["children"][1]["lines"][0] = line("Kulturdag i teatret", "trip", day="2026-11-12", retold=True)
    state = with_plans(state, "2026-11-08T19:30:00+01:00",
                       {("Beta", "2026-11-12"): "Teater hele dagen", ("Alfa", "2026-11-12"): "Kunst og tog"})
    lines = [day_lines(kid)["2026-11-12"] for kid in render.layout(state)[0].kids]
    assert lines[0][0] == "Kunst og tog"
    # Beta's trip is what the headline retells: it is said once, whatever the words.
    assert lines[1] == ["Kulturdag i teatret", "Medbring madpakke"]


def test_rain_on_a_trip_day_is_noted_unless_its_things_to_bring_are_rain_gear():
    state = heavy_week_state()
    model, _ = render.layout(state)
    thursday = [rain for kid in model.kids for iso, _, rain in kid.days if iso == "2026-11-12"]
    # Alfa packs rain gear; Beta does not.
    assert thursday == [False, True]


# --- Message topics ------------------------------------------------------------------------

class FakeClient:
    def __init__(self, topics=None, error=None):
        self.topics, self.error, self.documents = topics, error, []

    def generate(self, document, **kwargs):
        self.documents.append(document)
        if self.error:
            raise self.error
        return {"output": {"topics": self.topics}}


# --- Battery ---------------------------------------------------------------------------------


# --- The same conventions on the weekend cover and the children's day view ------------------


def yellow_used(image):
    """Yellow on the right half; the season's artwork on the left uses every pigment."""
    return 0x2 in {index for _count, index in image.crop((341, 0, 800, 480)).getcolors(maxcolors=256)}


def test_weekend_rows_draw_their_boxes_and_labels_from_their_facts():
    from tests_python.test_weather import weather_state

    state = weather_state("Blandet")
    # 'Skovdag' has no trip word: only its fact makes it a trip.
    state["weekend"] = [weekend_row("2026-10-03", "Lør. 3. okt.", "ChildA",
                                    line("Skovdag", "trip", day="2026-10-03"),
                                    line("madpakke", "bring", day="2026-10-03"))]
    dashboard.validate(state)
    [(_, who, items)] = render.weekend_activities(state)
    assert who == "ChildA" and items[0].chip == "TUR"
    assert items[1].runs == [("Medbring", True), (" madpakke", False)]


def test_the_board_names_a_missing_part_in_one_line():
    from skoleklar import health

    state = heavy_week_state()
    html = dashboard.render_html(state)
    assert "Mangler" not in html
    state = health.record(state, "weather", health.degraded(KeyError("private detail")))
    state = health.record(state, "composition", health.degraded("plain_board"))
    html = dashboard.render_html(state)
    assert "Mangler: enkel visning, vejret · Opdateret 08/11 19.30" in html
    assert "private detail" not in html


def code_strings(module):
    """The string literals a module's code uses, leaving out its docstrings."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(module.__file__).read_text())
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    return [node.value.casefold() for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings]


def test_the_renderer_holds_no_danish_word_lists():
    """No word in the renderer's code decides what a line is: `present` did."""
    strings = " ".join(code_strings(render))
    for word in ("medbring", "valgfrit", "frist", "matematik", "morgensang", "besked:", "lektie", "husk",
                 "samtale", "fødselsdag", "kulturdag", "udflugt"):
        assert word not in strings, word


# --- A Friday before the next week's plan is out ------------------------------------------

class _FridayMorning(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat("2026-11-20T08:30:00+01:00").astimezone(tz)


def friday_state(later=True):
    """Friday 20 November 2026, 08.30: this week's plans are out, next week's are not."""
    fri, composed = "2026-11-20", "2026-11-20T08:30:00+01:00"
    alfa = [hw("læs 15 min", "Dansk", fri), line("idrætstøj", "bring", day=fri)]
    beta = [hw("s. 48–49", "Matematik", fri)]
    week = [f"2026-11-{value}" for value in (16, 17, 18, 19)]
    summaries = [{"date": iso, "children": [
        {"name": name, "status": "ok", "headline": f"{name} plan {iso[-2:]}", "text": "Kort."} for name in NAMES]}
        for iso in week]
    summaries.append({"date": fri, "children": [
        {"name": name, "status": "ok", "headline": f"{name} fredag", "text": f"{name} læser og synger i salen."}
        for name in NAMES]})
    summaries += [{"date": f"2026-11-{value}", "children": [
        {"name": name, "status": "not_published", "headline": None, "text": None} for name in NAMES]}
        for value in (23, 24, 25, 26)]
    return {
        "schema_version": 3, "week_label": "UGE 47", "updated_label": "DATA FRE 20. NOV. · 08.30",
        "freshness": {"source_updated_at": composed, "composed_at": composed,
                      "expires_at": "2026-11-21T20:30:00+01:00", "status": "fresh"},
        "hero": {"state": "I dag", "day": "Fredag 20. nov.", "date": fri,
                 "children": [child("Alfa", alfa), child("Beta", beta)]},
        "weekdays": [day(iso, "Dag", [], []) for iso in week] + [day(fri, "Fre. 20. nov.", alfa, beta)],
        "ahead": [{"date": "2026-11-24", "children": [
            child("Alfa", [line("betal 20 kr", "deadline", day="2026-11-24")]), child("Beta", [])], "shared": []}],
        "weekend": [], "weekend_overflow": 0,
        "messages": {"checked": True, "bodies_retained": 0, "unread_count": 0, "items": []},
        "day_summaries": summaries,
        "later_events": [
            {"date": "2026-12-01", "who": "Beta", "line": line("Forældremøde 19.00", "meeting", day="2026-12-01")},
            {"date": "2026-12-03", "who": "Begge", "line": line("Juleklip 17.00", "party", day="2026-12-03")},
        ] if later else [],
    }


def right_texts(rows):
    return [(row.label, "".join(text for item in row.items for text, _ in item.runs)) for row in rows]


def test_a_week_without_a_plan_says_so_once_and_fills_with_the_week_that_went(monkeypatch):
    monkeypatch.setattr(dashboard, "datetime", _FridayMorning)
    state = friday_state()
    dashboard.validate(state)
    model, heights = render.layout(state)
    alfa = render.kid_rows(model.kids[0], model.tight)
    texts = right_texts(alfa)
    # This week first, oldest to newest, then one dotted rule and what is ahead.
    assert texts[0] == ("", "Ugen der gik")
    assert [label for label, _ in texts[1:5]] == ["Man 16.", "Tir 17.", "Ons 18.", "Tor 19."]
    assert texts[5] == ("Man 23.", "ingen ugeplan endnu") and alfa[5].items[0].chip == "UGE 48"
    assert alfa[5].items[0].outline and not alfa[5].items[0].box
    assert [row.rule for row in alfa] == [False] * 5 + [True] + [False] * (len(alfa) - 6)
    # The recap only fills room: it never folds a later event into '+N senere'.
    assert all(kid.later_folded == 0 and kid.dropped_right == 0 for kid in model.kids)
    assert ("Tir 1.", "Forældremøde 19.00") in right_texts(render.kid_rows(model.kids[1], model.tight))
    for kid, height in zip(model.kids, heights):
        rows = render.kid_rows(kid, model.tight)
        assert render.right_height(rows, model.tight) <= height - render.DIVIDER - sum(render.RIGHT_PAD)
    image, texts = drawn_texts(monkeypatch, state)
    assert "UGE 48" in texts and "Ugen der gik" in texts
    html = dashboard.render_html(state)
    assert 'class="chip open">UGE 48<' in html and "Ugen der gik" in html


def test_the_recap_gives_way_oldest_first():
    model = render.build_model(friday_state())
    kid = model.kids[0]
    full = render.right_height(render.kid_rows(kid, False), False)
    render.fill_recap(model, [full + render.DIVIDER + sum(render.RIGHT_PAD) - 22, 10_000])
    assert [iso for iso, _ in kid.recap] == ["2026-11-17", "2026-11-18", "2026-11-19"]


def test_a_week_with_its_plan_out_has_no_tag_and_no_recap(monkeypatch):
    monkeypatch.setattr(dashboard, "datetime", _FridayMorning)
    state = friday_state()
    for entry in state["day_summaries"][-4:]:
        entry["children"] = [{"name": name, "status": "ok", "headline": "Næste uge", "text": "Tekst."}
                             for name in NAMES]
    model, _ = render.layout(state)
    assert all(not kid.recap for kid in model.kids)
    assert all(item.kind != "missing" for kid in model.kids for _, items, _ in kid.days for item in items)


def test_the_next_school_days_plan_paragraph_shows_while_the_board_has_room(monkeypatch):
    monkeypatch.setattr(dashboard, "datetime", _FridayMorning)
    model, _ = render.layout(friday_state())
    width = render.LEFT_END - render.LEFT_X
    assert render.plan_lines(model.kids[0], width) == ["Alfa læser og synger i salen."]
    # A paragraph longer than five lines falls back to the headline; so does a busy board.
    model.kids[0].plan_text = "ord " * 200
    assert render.plan_lines(model.kids[0], width) == ["Alfa fredag"]
    busy = render.build_model(friday_state())
    busy.kids[0].plan_text = "Et langt afsnit om dagen. " * 6
    busy.kids[0].left = busy.kids[0].left * 6
    render.fit_board(busy)
    assert busy.kids[0].plan_text == ""
