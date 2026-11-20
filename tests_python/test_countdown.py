"""The parents' board's countdown: built-in dates, the household's own, wording and fit.
Synthetic names and dates only."""
from datetime import date

import pytest

from skolekit.profile import ProfileError
from skoleklar import countdown as cd
from skoleklar import dashboard_renderer as dashboard
from skoleklar import parents_board_render as render
from tests_python.test_parents_board import _FridayMorning, drawn_texts, friday_state

ZONE = dashboard.ZONE


def on(value, config=cd.Countdown()):
    found = cd.line(config, date.fromisoformat(value), ZONE)
    return None if found is None else f"{found['lead']} {found['text']}" + (" ⚑" if found["flag"] else "")


def test_easter_and_the_days_that_hang_on_it():
    assert [cd.easter(year).isoformat() for year in (2025, 2026, 2027)] == ["2025-04-20", "2026-04-05", "2027-03-28"]
    assert on("2027-01-24") == "14 dage til fastelavn"
    assert on("2027-05-05") == "Fri i morgen: Kr. himmelfart"
    assert on("2027-05-17") == "Fri i dag: 2. pinsedag"


@pytest.mark.parametrize("year, expected", [
    (2024, ("03-20", "06-20", "09-22", "12-21")),
    (2026, ("03-20", "06-21", "09-23", "12-21")),
    (2027, ("03-20", "06-21", "09-23", "12-22")),
])
def test_solstices_and_equinoxes_fall_on_the_danish_date(year, expected):
    assert tuple(cd.season_start(year, month, ZONE).strftime("%m-%d") for month in (3, 6, 9, 12)) == expected


def test_each_date_shows_from_its_own_window_and_the_nearest_wins():
    assert on("2026-11-03") == "7 dage til mortensaften"
    assert on("2026-11-09") == "I morgen: mortensaften"
    # Juleaften counts 45 days, but mortensaften is nearer until it is over.
    assert on("2026-11-10") == "I dag: mortensaften"
    assert on("2026-11-11") == "43 dage til juleaften"
    # A solstice shows on its day only, and says what it means.
    assert on("2026-12-20") == "4 dage til juleaften"
    assert on("2026-12-21") == "Solhverv: lyset vender"
    assert on("2027-06-21") == "Solhverv: længste dag"
    assert on("2026-09-23") == "Jævndøgn: dag = nat"
    assert on("2026-12-25") == "6 dage til nytårsaften"
    assert on("2027-08-01") is None


def test_the_households_birthdays_and_dates():
    config = cd.parse_countdown({"countdown": {
        "birthdays": [{"name": "Alfa", "date": "11-10"}, {"name": "Mor", "date": "02-29", "window": 3}],
        "dates": [{"name": "juleferie", "date": "2026-12-19", "window": 21}]}})
    # A birthday on a built-in date wins the day.
    assert on("2026-11-10", config) == "Tillykke, Alfa! ⚑"
    assert on("2026-11-09", config) == "I morgen: Alfa ⚑"
    assert on("2026-10-15", config) == "26 dage til Alfa ⚑"
    assert on("2026-12-01", config) == "18 dage til juleferie"
    # A date with a year is that year only.
    assert on("2027-12-01", config) == "23 dage til juleaften"
    # 29 February is the 28th in other years.
    assert on("2027-02-28", config) == "Tillykke, Mor! ⚑"
    without = cd.parse_countdown({"countdown": {"builtin": False}})
    assert on("2026-12-20", without) is None


@pytest.mark.parametrize("table, message", [
    ({"birthdays": [{"name": "Mormor og Morfar", "date": "05-01"}]}, "too long for the board"),
    ({"birthdays": [{"name": "Alfa", "date": "2018-05-01"}]}, "date is 'MM-DD'"),
    ({"dates": [{"name": "ferie", "date": "02-30"}]}, "is not a date"),
    ({"dates": [{"name": "ferie", "date": "07-01", "window": 90}]}, "window"),
    ({"birthdays": [{"name": "Alfa", "date": "05-01", "age": 9}]}, "unknown key 'age'"),
    ({"themes": True}, "unknown key 'themes'"),
])
def test_a_bad_entry_stops_with_its_name(table, message):
    with pytest.raises(ProfileError, match=message):
        cd.parse_countdown({"countdown": table})


def test_only_the_wordings_a_window_can_reach_are_measured():
    # On the day only: 'I dag: Mormor og Morfar' fits; 'I morgen: …' could never show.
    cd.parse_countdown({"countdown": {"dates": [{"name": "Mormor og Morfar", "date": "05-01", "window": 0}]}})
    with pytest.raises(ProfileError, match="too long"):
        cd.parse_countdown({"countdown": {"dates": [{"name": "Mormor og Morfar", "date": "05-01", "window": 1}]}})


def test_skoleklar_toml_accepts_the_countdown_table(tmp_path):
    from skoleklar.meaning import load_meaning
    (tmp_path / "skoleklar.toml").write_text('[[countdown.birthdays]]\nname = "Alfa"\ndate = "03-14"\n')
    load_meaning(tmp_path)
    assert cd.load_countdown(tmp_path).marks[0].name == "Alfa"
    assert cd.load_countdown(tmp_path / "missing").builtin


def test_the_board_draws_the_line_and_the_status_wins_when_both_do_not_fit(monkeypatch):
    monkeypatch.setattr(dashboard, "datetime", _FridayMorning)
    state = {**friday_state(), "countdown": {"lead": "34", "text": "dage til juleaften", "flag": False}}
    dashboard.validate(state)
    _, texts = drawn_texts(monkeypatch, state)
    assert "34" in texts and "dage til juleaften" in texts
    assert '<span class="pb-count"><b>34</b> dage til juleaften</span>' in dashboard.render_html(state)
    from skoleklar import health
    busy = health.record({**state, "review_count": 3}, "weather", health.degraded("timeout"))
    _, texts = drawn_texts(monkeypatch, busy)
    assert "Mangler: vejret" in texts
    # The browser board leaves it out by the same rule, never clipped.
    assert 'class="pb-count"' not in dashboard.render_html(busy)
    assert "dage til juleaften" not in texts


@pytest.mark.parametrize("countdown", [
    {"lead": "34", "text": "dage til juleaften"},
    {"lead": "34", "text": "dage til juleaften", "flag": False, "date": "2026-12-24"},
    {"lead": "", "text": "x", "flag": False},
    {"lead": "34", "text": "x" * 41, "flag": False},
])
def test_the_state_carries_only_the_line(countdown):
    with pytest.raises(ValueError, match="countdown"):
        dashboard.validate({**friday_state(), "countdown": countdown})
