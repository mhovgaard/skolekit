"""The parents' board: line kinds, the days ahead, later events, urgency, the drop
order and the message topics. Synthetic data only; no school data or network."""
from dataclasses import replace
from datetime import datetime

import pytest

from skoleklar import dashboard_renderer as dashboard
from skoleklar import parents_board as pb
from skoleklar import parents_board_render as render
from skoleklar.live_subjects import DisplayMessage, with_topics
from skoleklar.triage.message_topics import topic_writer, topics_mode, valid_topic

NAMES = ["Alfa", "Beta"]


class _SundayEvening(datetime):
    """The clock when the heavy week was composed, so its freshness never runs out."""

    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat("2026-11-08T19:30:00+01:00").astimezone(tz)


@pytest.fixture(autouse=True)
def _frozen_board_clock(monkeypatch):
    monkeypatch.setattr(dashboard, "datetime", _SundayEvening)


def child(name, lines, kinds, heading="Lektier"):
    return {"name": name, "heading": heading, "lines": lines, "kinds": kinds}


def day(iso, label, august, rita):
    return {"date": iso, "day": label, "children": [august, rita], "shared": None, "summary": "Lektier."}


def heavy_week_state():
    """Sunday 8 November 2026, 19.30: homework in two subjects every day but Thursday's trips."""
    return {
        "schema_version": 2, "week_label": "UGE 46", "updated_label": "DATA SØN 8. NOV. · 19.30",
        "freshness": {"source_updated_at": "2026-11-08T19:30:00+01:00",
                      "composed_at": "2026-11-08T19:30:00+01:00",
                      "expires_at": "2026-11-10T07:30:00+01:00", "status": "fresh"},
        "hero": {"state": "Næste skoledag", "day": "Mandag 9. nov.", "children": [
            child("Alfa", ["Dansk: læs 15 min i læsebogen", "Mat.: arbejdsbog s. 24",
                             "Frist: aflever samtykke til turen", "Medbring: idrætstøj"],
                  ["homework", "homework", "deadline", "bring"]),
            child("Beta", ["Matematik: ark med 7-tabellen", "Dansk: læs s. 88–90", "Skolehjem 15.00–17.00"],
                  ["homework", "homework", "info"])]},
        "horizon_label": "Skoleugen 9.–13. nov.", "past_summary": "",
        "weekdays": [
            day("2026-11-09", "Man. 9. nov.",
                child("Alfa", ["Dansk: læs 15 min i læsebogen", "Mat.: arbejdsbog s. 24"], ["homework"] * 2),
                child("Beta", ["Matematik: ark med 7-tabellen", "Dansk: læs s. 88–90"], ["homework"] * 2)),
            day("2026-11-10", "Tir. 10. nov.",
                child("Alfa", ["Dansk: øv ugens 5 stavord", "Engelsk: lær farverne"], ["homework"] * 2),
                child("Beta", ["Engelsk: 10 gloser", "Dansk: skriv 5 sætninger om en fabel", "Medbring: badetøj"],
                      ["homework", "homework", "bring"])),
            day("2026-11-11", "Ons. 11. nov.",
                child("Alfa", ["Matematik: s. 25–26", "N/T: tegn et efterårstræ"], ["homework"] * 2),
                child("Beta", ["Matematik: s. 46–47", "N/T: svar på spørgsmål s. 12"], ["homework"] * 2)),
            day("2026-11-12", "Tor. 12. nov.",
                child("Alfa", ["Besked: Kulturdag på kunstmuseet, hjem ca. 14", "Mødetid 7.45 ved bussen",
                                 "Medbring: madpakke, drikkedunk og regntøj"], ["trip", "change", "bring"], "Tur"),
                child("Beta", ["Besked: Kulturdag i teatret", "Medbring: madpakke"], ["trip", "bring"], "Tur")),
            day("2026-11-13", "Fre. 13. nov.",
                child("Alfa", ["Dansk: læs 15 min", "Matematik: s. 27"], ["homework"] * 2),
                child("Beta", ["Matematik: s. 48", "Engelsk: læs teksten s. 14", "Medbring: idrætstøj"],
                      ["homework", "homework", "bring"])),
        ],
        "weekend": [], "weekend_overflow": 0, "footer": "",
        "messages": {"checked": True, "bodies_retained": 0, "unread_count": 4, "items": []},
        "day_weather": [{"date": "2026-11-09", "icon": "cloud", "low": 1, "high": 5, "rain_mm": 0},
                        {"date": "2026-11-12", "icon": "rain", "low": 7, "high": 9, "rain_mm": 3}],
        "day_summaries": [{"date": "2026-11-09", "children": [
            {"name": "Alfa", "status": "ok", "headline": "Bogstavet F og tal til 20", "text": "Tekst."},
            {"name": "Beta", "status": "ok", "headline": "7-tabellen og fabler", "text": "Tekst."}]}],
        "later_events": [
            {"date": "2026-11-16", "who": "Begge", "kind": "deadline", "text": "Frist: tilmeld skolefest"},
            {"date": "2026-11-19", "who": "Begge", "kind": "party", "text": "Skolefest 17.00–19.30"},
            {"date": "2026-11-20", "who": "Beta", "kind": "meeting", "text": "Forældresamtale 16.40"},
            {"date": "2026-11-23", "who": "Alfa", "kind": "meeting", "text": "Forældremøde 19.00"}],
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


# --- What each line is -------------------------------------------------------------------

@pytest.mark.parametrize("title, kind", [
    ("Forældremøde 19.00", "meeting"), ("Forældresamtale kl. 16.40", "meeting"),
    ("Tur til kunstmuseet, mødetid 7.45", "trip"), ("Skovtur", "trip"), ("Kulturdag i teatret", "trip"),
    ("Skolefest", "party"), ("Fødselsdagsfest", "party"), ("Klassearrangement", "event"),
    ("Naturfag", "event"), ("Fodboldturnering", "event"),
])
def test_event_titles_get_a_category(title, kind):
    assert pb.event_kind(title) == kind


def test_fact_kinds_follow_type_and_actionability():
    assert pb.fact_kind({"type": "homework"}) == "homework"
    assert pb.fact_kind({"type": "bring", "actionability": "required"}) == "bring"
    assert pb.fact_kind({"type": "bring", "actionability": "recommended"}) == "optional"
    assert pb.fact_kind({"type": "parent_action", "actionability": "required", "title": "Tilmeld skolefest"}) \
        == "deadline"
    assert pb.fact_kind({"type": "parent_action", "title": "Forældresamtale kl. 16.40"}) == "meeting"
    assert pb.fact_kind({"type": "event", "activity_kind": "class_trip", "title": "Klassetur"}) == "trip"
    assert pb.fact_kind({"type": "schedule_change"}) == "change"
    # One line that joins a trip and its packing list is the trip.
    assert pb.line_kind([{"type": "bring"}, {"type": "event", "title": "Skovtur"}]) == "trip"


def test_kinds_come_from_the_facts_named_in_the_audit():
    state = heavy_week_state()
    for section in [state["hero"], *state["weekdays"]]:
        for entry in section["children"]:
            entry.pop("kinds")
    context = {"items": [{"ref": "fact-1", "type": "homework"},
                         {"ref": "fact-2", "type": "event", "title": "Kulturdag på kunstmuseet"}]}
    audit = {"facts": [
        {"ref": "fact-1", "decision": "displayed", "location": "hero.children[0].lines[0]"},
        {"ref": "fact-2", "decision": "displayed", "location": "weekdays[3].children[0].lines[0]"}]}
    pb.attach_kinds(state, audit, context)
    assert state["hero"]["children"][0]["kinds"][:3] == ["homework", "info", "deadline"]
    assert state["weekdays"][3]["children"][0]["kinds"][0] == "trip"
    assert state["weekdays"][3]["children"][0]["kinds"][2] == "bring"  # from its 'Medbring:' marker
    dashboard.validate(state)


def test_an_events_own_words_can_make_it_a_meeting():
    assert render.make_item("Skolehjemssamtaler", "event", "2026-11-09", None).chip == "MØDE"
    assert render.make_item("Klassearrangement", "event", "2026-11-09", None).chip == "ARR."
    assert render.make_item("Fødselsdag hos Dagny", "trip", "2026-11-09", None).chip == "TUR"


def test_kinds_must_match_the_lines():
    state = heavy_week_state()
    state["hero"]["children"][0]["kinds"].pop()
    with pytest.raises(ValueError, match="kinds"):
        dashboard.validate(state)
    state = heavy_week_state()
    state["hero"]["children"][0]["kinds"][0] = "secret"
    with pytest.raises(ValueError, match="kinds"):
        dashboard.validate(state)


# --- Days ahead and later events ------------------------------------------------------------

def context_at(now, items):
    return {"now": now, "items": items}


def fact(ref, child, day, kind="homework", title="Læs s. 4", **extra):
    return {"ref": ref, "child": child, "effective_date": day, "type": kind, "title": title,
            "actionability": "required", **extra}


def test_thursday_evening_looks_into_the_weekend_and_next_week():
    state = heavy_week_state()  # composed for 9–13 November
    context = context_at("2026-11-12T19:30:00+01:00", [
        fact("f1", "Alfa", "2026-11-14", "event", "Fødselsdagsfest", activity_kind="birthday_party"),
        fact("f2", "Beta", "2026-11-16", "homework", "Læs s. 90"),
        fact("f3", "Beta", "2026-11-17", "bring", "Medbring badetøj"),
        fact("f4", "Alfa", "2026-11-13", "homework", "Already in the week"),
        fact("f5", "Beta", "2026-11-18", "homework", "After the look-ahead"),
        fact("f6", "Alfa", "2026-11-16", "homework", "Uncertain", needs_review=True),
        fact("f7", "Elsewhere", "2026-11-16", "event", "Skolefest"),
    ])
    rows = pb.ahead_rows(context, state)
    assert [row["date"] for row in rows] == ["2026-11-14", "2026-11-16", "2026-11-17"]
    saturday, monday, tuesday = rows
    assert saturday["children"][0] == {"name": "Alfa", "lines": ["Fødselsdagsfest"], "kinds": ["party"]}
    assert monday["children"][1]["lines"] == ["Læs s. 90"] and monday["children"][0]["lines"] == []
    assert monday["shared"] == [{"text": "Skolefest", "kind": "party"}]
    assert tuesday["children"][1] == {"name": "Beta", "lines": ["Medbring: badetøj"], "kinds": ["bring"]}


def test_later_events_are_trips_parties_meetings_and_deadlines_two_weeks_out():
    state = heavy_week_state()
    context = context_at("2026-11-08T19:30:00+01:00", [
        fact("f1", "Alfa", "2026-11-12", "event", "Inside the look-ahead"),
        fact("f2", "Alfa", "2026-11-19", "event", "Skolefest"),
        fact("f3", "Beta", "2026-11-19", "event", "Skolefest"),
        fact("f4", "Beta", "2026-11-20", "parent_action", "Forældresamtale 16.40"),
        fact("f5", "Alfa", "2026-11-16", "parent_action", "Tilmeld skolefest"),
        fact("f6", "Alfa", "2026-11-18", "homework", "Homework is not an event"),
        fact("f7", "Alfa", "2026-11-24", "event", "Too far out"),
        fact("f8", "Beta", "2026-11-23", "event", "Juleklip", cancelled=True),
    ])
    assert pb.later_events(context, state) == [
        {"date": "2026-11-16", "kind": "deadline", "text": "Tilmeld skolefest", "who": "Alfa"},
        {"date": "2026-11-19", "kind": "party", "text": "Skolefest", "who": "Begge"},
        {"date": "2026-11-20", "kind": "meeting", "text": "Forældresamtale 16.40", "who": "Beta"},
        {"date": "2026-11-23", "kind": "party", "text": "Juleklip aflyst", "who": "Beta"},
    ]


def test_a_weekly_event_is_not_repeated_below_the_rule():
    state = heavy_week_state()
    context = context_at("2026-11-08T19:30:00+01:00", [
        fact("f1", "Elsewhere", "2026-11-13", "event", "Morgensang"),
        fact("f2", "Elsewhere", "2026-11-20", "event", "Morgensang"),
        fact("f3", "Beta", "2026-11-16", "event", "Svømning"),
        fact("f4", "Beta", "2026-11-23", "event", "Svømning"),
    ])
    assert pb.later_events(context, state) == [
        {"date": "2026-11-16", "kind": "event", "text": "Svømning", "who": "Beta"}]


def test_morning_singing_is_a_routine_and_never_shown_unless_cancelled():
    state = heavy_week_state()
    state["weekdays"][4]["shared"] = "Morgensang"
    state["weekdays"][4]["shared_kind"] = "event"
    state["later_events"].append({"date": "2026-11-20", "who": "Begge", "kind": "event", "text": "Morgensang"})
    state["later_events"].sort(key=lambda entry: entry["date"])
    assert "Morgensang" not in dashboard.render_html(state)
    state["weekdays"][4]["shared"] = "Morgensang aflyst"
    assert "Morgensang aflyst" in dashboard.render_html(state)


def test_look_ahead_is_left_out_of_the_weekend_cover_and_day_view():
    state = heavy_week_state()
    context = context_at("2026-11-08T19:30:00+01:00", [fact("f1", "Beta", "2026-11-19", "event", "Skolefest")])
    assert "later_events" in pb.attach_lookahead({**state, "later_events": []}, context)
    cover = {**state, "weather": {"place": "Byen"}}
    assert pb.attach_lookahead(cover, context) == cover


@pytest.mark.parametrize("change", [
    {"later_events": [{"date": "2026-11-19", "who": "Somebody", "kind": "party", "text": "Fest"}]},
    {"later_events": [{"date": "2026-11-19", "who": "Begge", "kind": "homework", "text": "Læs"}]},
    {"ahead": [{"date": "2026-11-14", "children": [], "shared": []}]},
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


def test_yellow_marks_only_time_urgency():
    model, _ = render.layout(heavy_week_state())
    urgent = [item for kid in model.kids for item in kid.left if item.urgent]
    urgent += [item for kid in model.kids for _, items, _ in kid.days for item in items if item.urgent]
    # Monday's deadline is urgent; Thursday's changed time is four days away, not yet.
    assert [item.kind for item in urgent] == ["deadline"]
    assert render.make_item("Mødetid 7.45", "change", "2026-11-10", render.date(2026, 11, 8)).urgent
    assert not render.make_item("Mødetid 7.45", "change", "2026-11-11", render.date(2026, 11, 8)).urgent
    # A moved lesson without a new time is not time-urgent.
    assert not render.make_item("Idræt ved søen", "change", "2026-11-09", render.date(2026, 11, 8)).urgent
    assert render.make_item("Fri kl. 12", "change", "2026-11-09", render.date(2026, 11, 8)).urgent


def test_full_week_gives_way_in_the_fixed_order_and_keeps_homework():
    state = heavy_week_state()
    for entry in state["weekdays"]:
        for kid in entry["children"]:
            if len(kid["lines"]) < 3:
                kid["lines"].append("Vikar: Alberte · DAN 1")
                kid["kinds"].append("substitute")
    model, heights = render.layout(state)
    assert sum(heights) <= 480 - render.HEADER_HEIGHT
    shown = [item for kid in model.kids for _, items, _ in kid.days for item in items]
    assert not any(item.kind == "substitute" for item in shown)
    assert sum(item.kind == "homework" for item in shown) == 12
    assert any(item.chip == "TUR" for item in shown)
    assert sum(kid.dropped_right for kid in model.kids) > 0


def test_weekend_activity_before_the_next_school_day_stays_when_the_forecast_fails():
    state = heavy_week_state()
    state["freshness"]["composed_at"] = "2026-11-07T10:00:00+01:00"  # Saturday, no weekend weather
    state["weekend"] = [{"day": "Søn. 8. nov.", "text": "Beta: Fødselsdagsfest"}]
    html = dashboard.render_html(state)
    rita = html.split('<section class="pb-cell pb-right">')[2]
    assert '<span class="chip">FEST</span>Fødselsdagsfest' in rita
    assert rita.index("Søn 8.") < rita.index("Tir 10.")


@pytest.mark.parametrize("composed", ["2026-11-08T12:30:00+01:00", "2026-11-08T19:30:00+01:00"])
def test_a_board_for_monday_never_lists_sunday(composed):
    state = heavy_week_state()
    state["freshness"]["composed_at"] = composed
    state["weekend"] = [{"day": "Søn. 8. nov.", "text": "Beta: Fødselsdagsfest"}]
    state["weekend_overflow"] = 1
    html = dashboard.render_html(state)
    assert "Fødselsdagsfest" not in html and "Søn 8." not in html and "mere" not in html.split("Begge")[-1]


def test_weekend_overflow_is_counted_on_the_last_activity():
    state = heavy_week_state()
    state["freshness"]["composed_at"] = "2026-11-07T10:00:00+01:00"
    state["weekend"] = [{"day": "Lør. 7. nov.", "text": "Alfa: Fodboldkamp kl. 11"},
                        {"day": "Søn. 8. nov.", "text": "Beta: Fødselsdagsfest"}]
    state["weekend_overflow"] = 1
    html = dashboard.render_html(state)
    # Both activities fit; the one the weekend rows had no room for is counted, not listed.
    assert "Fodboldkamp kl. 11" in html and "Fødselsdagsfest (+1 mere)" in html
    assert "aktivitet mere" not in html


def test_message_line_without_a_topic_shows_only_sender_and_date():
    state = heavy_week_state()
    messages = [replace(item, topic="") for item in live_messages()]
    html = dashboard.render_html(state, live_messages=messages)
    assert ">Karen<" in html and "Husk samtykke" not in html


# --- Message topics ------------------------------------------------------------------------

class FakeClient:
    def __init__(self, topics=None, error=None):
        self.topics, self.error, self.documents = topics, error, []

    def generate(self, document, **kwargs):
        self.documents.append(document)
        if self.error:
            raise self.error
        return {"output": {"topics": self.topics}}


def test_topics_are_written_only_from_safe_subjects():
    client = FakeClient([{"id": "1", "topic": "Samtykke tur", "hide": False}])
    messages = [DisplayMessage("Alfa", "Karen H.", "Husk samtykke til turen", "safe", "08/11", True),
                DisplayMessage("Beta", "Simon B.", "", "redacted", "06/11", False)]
    result = with_topics(messages, topic_writer(client))
    assert client.documents == [{"subjects": [{"id": "1", "subject": "Husk samtykke til turen"}]}]
    assert result[0].topic == "Samtykke tur" and result[1] == messages[1]


def test_a_hidden_topic_redacts_the_subject():
    client = FakeClient([{"id": "1", "topic": None, "hide": True}])
    [message] = with_topics([DisplayMessage("Beta", "Simon B.", "Om pigerne", "safe", "06/11")],
                            topic_writer(client))
    assert message.status == "redacted" and message.text == "" and message.topic == ""


@pytest.mark.parametrize("topic", ["Beta fest", "Tre ord her", "Tur 12", "", "x" * 25, None])
def test_invalid_topics_are_dropped(topic):
    assert not valid_topic(topic, NAMES)


def test_a_failing_model_leaves_sender_and_date():
    messages = [DisplayMessage("Alfa", "Karen H.", "Husk samtykke", "safe", "08/11", True)]
    assert with_topics(messages, topic_writer(FakeClient(error=RuntimeError("down")))) == messages


def test_topics_are_opt_in(monkeypatch):
    monkeypatch.delenv("AG_MESSAGE_TOPICS", raising=False)
    assert topics_mode() == "off"
    monkeypatch.setenv("AG_MESSAGE_TOPICS", "model")
    assert topics_mode() == "model"
    monkeypatch.setenv("AG_MESSAGE_TOPICS", "always")
    with pytest.raises(ValueError):
        topics_mode()


# --- Battery ---------------------------------------------------------------------------------

def test_battery_comes_from_a_recent_status_report(monkeypatch):
    from datetime import datetime

    from skoleklar import board_status
    from skoleklar.runtime import scan as scan_service

    now = datetime.fromisoformat("2026-11-08T19:30:00+01:00")
    record = {"received_at": "2026-11-08T18:00:00+00:00", "report": {"battery_present": True,
                                                                    "battery_percent": 81.6}}
    monkeypatch.setattr(board_status, "status_store_from_environment", lambda: object())
    monkeypatch.setattr(board_status, "latest_record", lambda store: record)
    state = heavy_week_state()
    state.pop("device")
    frames = [(now, state)]
    assert scan_service._with_battery(frames, now)[0][1]["device"] == {"battery_percent": 82}
    record["received_at"] = "2026-11-05T18:00:00+00:00"
    assert "device" not in scan_service._with_battery(frames, now)[0][1]
    monkeypatch.setattr(board_status, "latest_record", lambda store: 1 / 0)
    assert scan_service._with_battery(frames, now) == frames


# --- The same conventions on the weekend cover and the children's day view ------------------

def test_every_view_shows_the_same_status_with_the_battery():
    from tests_python.test_day_view import rendered_state
    from tests_python.test_weather import weather_state

    for state in (weather_state("Blandet"), rendered_state()):
        state["device"] = {"battery_percent": 64}
        html = dashboard.render_html(state)
        assert 'class="pb-status' in html and "64 %" in html and "Opdateret " in html


def yellow_used(image):
    """Yellow on the right half; the season's artwork on the left uses every pigment."""
    return 0x2 in {index for _count, index in image.crop((341, 0, 800, 480)).getcolors(maxcolors=256)}


def test_the_day_view_marks_only_an_earlier_finish_yellow():
    from tests_python.test_day_view import rendered_state
    from skoleklar.day_view_render import ends_earlier

    state = rendered_state()
    children = state["day_view"]["children"]
    assert any(lesson["change"] == "vikar" for child in children for lesson in child["lessons"])
    # A substitute is named on its tile, not marked.
    assert not any(ends_earlier(child) for child in children)
    assert not yellow_used(dashboard.render_png(state, dither_mode="crisp"))
    assert 'class="ends earlier"' not in dashboard.render_html(state)
    # The last lesson cancelled: the day ends early, and that is time-urgent.
    lessons = children[1]["lessons"]
    lessons[-1].update(change="aflyst", note="")
    assert ends_earlier(children[1])
    assert yellow_used(dashboard.render_png(state, dither_mode="crisp"))
    assert 'class="ends earlier"' in dashboard.render_html(state)


def test_weekend_activities_get_boxes_and_count_the_rest_on_the_last():
    from tests_python.test_weather import weather_state

    state = weather_state("Blandet")
    state["weekend"] = [{"day": "Lør. 3. okt.", "text": "ChildA: Fødselsdagsfest; Medbring: udetøj"}]
    state["weekend_overflow"] = 2
    [(label, who, items)] = render.weekend_activities(state)
    assert (label, who) == ("Lør 3.", "ChildA")
    assert items[0].chip == "FEST" and items[1].runs[0] == ("Medbring", True)
    assert items[1].runs[-1] == (" (+2 mere)", False)


def test_weekend_day_labels_are_never_shortened(monkeypatch):
    from tests_python.test_weather import weather_state

    state = weather_state("Blandet")
    state["weekend"] = [{"day": "Lør. 3. okt.", "text": "ChildA: Fødselsdagsfest"},
                        {"day": "Søn. 4. okt.", "text": "Begge: Skovtur"}]
    drawn = []
    original = dashboard._draw_protected_text
    monkeypatch.setattr(dashboard, "_draw_protected_text",
                        lambda draw, xy, value, *rest: drawn.append(value) or original(draw, xy, value, *rest))
    dashboard.render_png(state)
    assert "Lør 3." in drawn and "Søn 4." in drawn
    assert not any(value.endswith("…") for value in drawn)
