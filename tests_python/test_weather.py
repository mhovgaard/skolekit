"""Weekend weather: when it shows, what it says, and that both renderers accept it."""
import copy
from datetime import date, datetime, timedelta

import pytest

from skoleklar import weather
from skoleklar.runtime import scan as scan_service
from skoleklar.dashboard_renderer import render_html, render_png, validate
from tests_python.test_dashboard_rendering import weekday_grid_state
from tests_python.weather_fixtures import SATURDAY, SCENARIOS, forecast

CONFIG = weather.WeatherConfig("Byen", 55.68, 12.57)
PRIVATE = weather.WeatherConfig("Byen", 55.68, 12.57, "private")
SATURDAY_MORNING = datetime.fromisoformat("2026-10-03T05:45:00+02:00")


def at(value):
    return datetime.fromisoformat(value + "+02:00")


def build(name, config=CONFIG, now=SATURDAY_MORNING):
    days = weather.weekend_dates(now)
    return weather.build_weather(forecast(*SCENARIOS[name]), days, config, now)


def weather_state(name="Udedag", config=CONFIG):
    result = weather.attach_weather(
        weekday_grid_state("2026-10-03T05:45:00+02:00"), SATURDAY_MORNING, config=config,
        fetch=lambda _config, _days: forecast(*SCENARIOS[name]))
    # Historical weather fixtures must not become inbox-stale as wall time moves on.
    result["freshness"]["expires_at"] = "2099-10-04T17:45:00+02:00"
    return result


@pytest.mark.parametrize("now,expected", [
    ("2026-10-01T19:30:00", []),                    # Thursday evening
    ("2026-10-02T05:45:00", []),                    # Friday morning: still a school day
    ("2026-10-02T19:30:00", [3, 4]),                # Friday evening
    ("2026-10-03T05:45:00", [3, 4]),                # Saturday morning
    ("2026-10-03T19:30:00", [4, 5]),                # Saturday evening: Sunday and Monday
    ("2026-10-04T05:45:00", [4, 5]),                # Sunday morning
    ("2026-10-04T11:59:00", [4, 5]),
    ("2026-10-04T12:00:00", []),                    # Sunday from noon: Monday matters again
])
def test_weekend_panel_runs_from_friday_afternoon_to_sunday_noon(now, expected):
    assert weather.weekend_dates(at(now)) == [date(2026, 10, day) for day in expected]


@pytest.mark.parametrize("name", ["Udedag", "Stranden", "Skoven", "Blandet", "Museum", "Indedag"])
def test_family_verdicts(name):
    result = build(name)
    assert result["verdict"] == name
    assert result["label"] == "Bedste tid ude"
    validate({**weekday_grid_state("2026-10-03T05:45:00+02:00"), "weather": result})


def test_cykeldag_is_private_only():
    family, private = build("Cykeldag"), build("Cykeldag", PRIVATE)
    assert family["verdict"] != "Cykeldag"
    # The family panel still carries the ride as a note.
    assert family["note"] == "Cykeltur: lør. 13–17 · lyst til 18:40"
    assert private["verdict"] == "Cykeldag"
    assert private["label"] == "Bedste cykeltid"
    assert private["reason"] == "Lørdag er dagen: tørt kl. 13–17, 15–17° og lyst til 18:40."
    assert "note" not in private


def test_private_mode_falls_back_to_family_verdict_without_a_ride():
    assert build("Indedag", PRIVATE)["verdict"] == "Indedag"
    assert "note" not in build("Indedag", PRIVATE)


def test_best_window_is_dry_and_inside_daylight():
    saturday, sunday = build("Cykeldag")["days"]
    assert saturday["hours"] == "ccssssssssssccc" and saturday["window"]["start"] == 13
    assert sunday["hours"] == "ccccsssccrrrrrr"
    start, end = sunday["window"]["start"], sunday["window"]["end"]
    assert "r" not in sunday["hours"][start - 6:end - 6]
    for day in (saturday, sunday):
        assert day["window"]["start"] >= 8 and day["window"]["end"] <= 18   # 07:16–18:40


def test_no_window_when_it_rains_all_day():
    assert [day["window"] for day in build("Indedag")["days"]] == [None, None]


@pytest.mark.parametrize("config", [CONFIG, PRIVATE])
@pytest.mark.parametrize("hour", [5, 11, 14, 17])
def test_refresh_keeps_the_full_day_trip_even_after_the_dry_window(config, hour):
    source = forecast(("rrrssssrrrrrrrr", 9, 18, 4), SCENARIOS["Indedag"][1])
    days = weather.weekend_dates(SATURDAY_MORNING)
    morning = weather.build_weather(source, days, config, SATURDAY_MORNING)
    later = weather.build_weather(source, days, config, at(f"2026-10-03T{hour:02}:30:00"))
    assert later == morning  # Window, verdict, flags and cycling note all retain the day view.
    assert later["days"][0]["window"] == {"start": 9, "end": 13, "label": "Tør · 12–15°"}
    state = {**weather_state(), "weather": later}
    saturday_html = render_html(state).split('<article class="wx-day">')[1].split('</article>')[0]
    assert ">9–13</text>" in saturday_html and "Ingen tør tur" not in saturday_html
    if config.mode == "private":
        assert later["verdict"] == "Cykeldag"
    else:
        assert "lør. 9–13" in later["note"]


def test_flags_are_limited_and_ordered_by_severity():
    saturday, sunday = build("Indedag")["days"]
    assert saturday["flags"] == [{"icon": "storm", "label": "Torden"}, {"icon": "wind", "label": "13 m/s"}]
    assert sunday["flags"][0] == {"icon": "wind", "label": "11 m/s"}
    assert build("Stranden")["days"][0]["flags"] == [{"icon": "uv", "label": "UV 7"}]
    assert build("Udedag")["days"][0]["flags"] == []


def test_cells_round_temperature_and_rain():
    cells = build("Museum")["days"][0]["cells"]
    assert [cell["icon"] for cell in cells] == ["rain", "rain"]
    assert all(type(cell["temp"]) is int and cell["rain_mm"] >= 1 for cell in cells)
    assert build("Udedag")["days"][0]["cells"][0] == {
        "label": "Formiddag", "icon": "sun", "temp": 14, "rain_mm": 0}


@pytest.mark.parametrize("now", ["2026-10-02T19:30:00", "2026-10-03T05:45:00",
                                  "2026-10-03T14:10:00", "2026-10-03T17:30:00"])
def test_weekend_cover_uses_daytime_even_when_morning_and_evening_are_wet(now):
    # Rain before 09 and from 16 onwards must not turn a sunny daytime into a rainy cover.
    source = forecast(("rrrsssssssrrrrr", 9, 18, 4), SCENARIOS["Udedag"][1])
    result = weather.build_weather(source, [SATURDAY, SATURDAY + timedelta(days=1)], CONFIG, at(now))
    day = result["days"][0]
    assert (day["icon"], day["low"], day["high"], day["rain_mm"]) == ("sun", 12, 18, 0)
    assert [cell["icon"] for cell in day["cells"]] == ["sun", "rain"]
    assert day["hours"] == "rrrsssssssrrrrr"
    state = {**weather_state(), "weather": result}
    left = render_html(state).split('<section class="weather">')[0]
    assert '<figure class="wx-cover"' in left and "<figcaption>" not in left
    assert render_png(state).size == (800, 480)


@pytest.mark.parametrize("daytime,expected", [
    ("sssssss", "sun"),
    ("ssspppp", "partly"),
    ("ccccccc", "cloud"),
    ("sssrsss", "sun"),            # One heavy shower does not describe the whole day.
    ("rrrrsss", "rain"),
    ("nnnnsss", "snow"),
    ("ffffsss", "fog"),
    ("ssstsss", "storm"),           # Retain a thunderstorm warning even if brief.
])
def test_weekend_cover_reflects_prevailing_daytime_weather(daytime, expected):
    source = forecast(("ccc" + daytime + "ccccc", 9, 18, 4), SCENARIOS["Udedag"][1])
    if daytime == "sssrsss":
        source["hourly"]["precipitation"][12] = 3.0
    day = weather.build_weather(source, weather.weekend_dates(SATURDAY_MORNING),
                                CONFIG, SATURDAY_MORNING)["days"][0]
    assert day["icon"] == expected
    if daytime == "sssrsss":
        assert day["rain_mm"] == 3


def test_older_weather_bundles_still_render():
    state = weather_state("Blandet")
    for day in state["weather"]["days"]:
        del day["rain_mm"]
    assert '<figure class="wx-cover"' in render_html(state)
    assert render_png(state).size == (800, 480)


def test_incomplete_forecast_is_an_error_not_a_guess():
    partial = forecast(SCENARIOS["Udedag"][0])   # Saturday only
    with pytest.raises(ValueError, match="does not cover the weekend"):
        weather.build_weather(partial, weather.weekend_dates(SATURDAY_MORNING), CONFIG, SATURDAY_MORNING)
    holes = forecast(*SCENARIOS["Udedag"])
    holes["hourly"]["temperature_2m"][10] = None
    with pytest.raises(ValueError, match="display hours"):
        weather.build_weather(holes, weather.weekend_dates(SATURDAY_MORNING), CONFIG, SATURDAY_MORNING)


def test_attach_only_during_the_weekend():
    def fetch(_config, _days):
        raise AssertionError("no forecast is fetched outside the weekend")

    state = weekday_grid_state("2026-09-30T08:00:00+02:00")
    assert weather.attach_weather(state, at("2026-09-30T08:00:00"), config=CONFIG, fetch=fetch) is state
    forced = weather.weekend_weather(
        at("2026-09-30T08:00:00"), config=CONFIG, force=True,
        fetch=lambda _config, days: forecast(*SCENARIOS["Udedag"]) if days == [SATURDAY, date(2026, 10, 4)] else {})
    assert forced["verdict"] == "Udedag"


def test_school_days_get_one_glyph_and_range_from_today_on():
    state = weekday_grid_state("2026-10-05T07:00:00+02:00")
    for offset, day in enumerate(state["weekdays"]):
        day["date"] = (SATURDAY + timedelta(days=offset - 1)).isoformat()
    seen = {}

    def fetch(_config, days):
        seen["days"] = days
        return forecast(*SCENARIOS["Blandet"])

    result = weather.attach_weather(state, at("2026-10-03T10:00:00"), config=CONFIG, fetch=fetch)
    assert "weather" in result and "day_weather" not in result  # the weekend panel wins
    result = weather.attach_weather(state, at("2026-10-04T16:00:00"), config=CONFIG, fetch=fetch)
    # Sunday afternoon: Friday and Saturday are past, and the fixture only covers the weekend.
    assert seen["days"][0] == SATURDAY + timedelta(days=1)
    assert [day["date"] for day in result["day_weather"]] == ["2026-10-04"]
    assert set(result["day_weather"][0]) == {"date", "icon", "low", "high", "rain_mm"}
    assert result["day_weather"][0]["icon"] in ("sun", "partly", "cloud", "rain")


def test_location_comes_from_the_environment(monkeypatch):
    for key in ("AG_WEATHER", "AG_WEATHER_PLACE", "AG_WEATHER_LATITUDE", "AG_WEATHER_LONGITUDE", "AG_WEATHER_MODE"):
        monkeypatch.delenv(key, raising=False)
    # The code holds no place of its own: nothing configured, no weather.
    assert weather.config_from_environment() is None
    monkeypatch.setenv("AG_WEATHER_PLACE", "Skagen")
    with pytest.raises(ValueError, match="LATITUDE"):
        weather.config_from_environment()
    monkeypatch.setenv("AG_WEATHER_PLACE", "Skagen")
    monkeypatch.setenv("AG_WEATHER_LATITUDE", "57.72512")
    monkeypatch.setenv("AG_WEATHER_LONGITUDE", "10.58394")
    monkeypatch.setenv("AG_WEATHER_MODE", "private")
    # Coordinates are coarsened before they can reach the forecast provider.
    assert weather.config_from_environment() == weather.WeatherConfig("Skagen", 57.73, 10.58, "private")
    monkeypatch.setenv("AG_WEATHER_MODE", "everyone")
    with pytest.raises(ValueError):
        weather.config_from_environment()
    monkeypatch.setenv("AG_WEATHER", "off")
    assert weather.config_from_environment() is None
    assert weather.weekend_weather(SATURDAY_MORNING) is None


def test_request_sends_only_coarse_coordinates(monkeypatch):
    import httpx
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return forecast(*SCENARIOS["Udedag"])

    def get(url, *, timeout, params):
        seen.update(url=url, params=params)
        return Response()

    monkeypatch.setattr(httpx, "get", get)
    result = weather.weekend_weather(SATURDAY_MORNING, config=CONFIG)
    assert result["verdict"] == "Udedag"
    assert seen["url"] == weather.API_URL
    assert seen["params"]["latitude"] == 55.68 and seen["params"]["longitude"] == 12.57
    assert (seen["params"]["start_date"], seen["params"]["end_date"]) == ("2026-10-03", "2026-10-04")
    assert set(seen["params"]) == {"latitude", "longitude", "hourly", "daily", "timezone",
                                   "wind_speed_unit", "start_date", "end_date"}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_png_stays_a_six_colour_panel_image(name):
    state = weather_state(name, PRIVATE if name == "Cykeldag" else CONFIG)
    image = render_png(state)
    assert image.size == (800, 480) and image.mode == "P"
    assert len(image.getcolors()) <= 6


def test_weekend_mode_puts_activities_left_and_weather_right():
    state = weather_state("Blandet")
    weekday = {key: value for key, value in state.items() if key != "weather"}
    with_weather, without = render_png(state), render_png(weekday)
    for box in ((0, 0, 340, 480), (341, 0, 800, 480)):
        assert with_weather.crop(box).tobytes() != without.crop(box).tobytes()
    page, weekday_page = render_html(state), render_html(weekday)
    left, right = page.split('<section class="weather">')
    # Left: today's weather as a cover, the plans and the inbox line. Nothing about school.
    assert '<div class="state">Weekend</div><h1>Lørdag 3. okt.</h1>' in left
    # The cover is the picture alone; the temperature is on the right-hand forecast.
    assert '<figure class="wx-cover" data-artwork=' in left and "<figcaption>" not in left
    assert '<img src="data:image/webp;base64,' in left
    # The parents' board's boxes on the activities, its message lines and total.
    assert ('<ul class="wk-list"><li><b>Lør 26.</b><span><b>Begge</b> <span class="chip">TUR</span>'
            'Familietur kl. 11</span></li></ul>') in left
    assert "Mandag" not in left and "Skolebog" not in left
    assert "7 ulæste i alt" in left and 'class="messages compact"' in left
    # Right: the weather instead of the school-week table.
    assert "Weekendvejr · Byen" in right and ">Blandet</h2>" in right
    assert right.count('class="wx-day"') == 2 and right.count('class="wx-arc"') == 2
    # Each day: the sun's arc with the trip inside, sunrise and sunset at its feet.
    assert '<text class="span" x="100" y="70">10–13</text>' in right and "Ingen tør tur" not in right
    assert right.count("07:16") == 2 and right.count("18:40") == 2
    assert 'class="week"' not in page and 'class="dashboard parents"' in weekday_page
    assert 'class="weather"' not in weekday_page and "wx-day\"" not in weekday_page
    assert "<script src" not in page and "http" not in page.split("</style>", 1)[1]


@pytest.mark.parametrize("rain_mm", [1, 12, 100, 9999])
@pytest.mark.parametrize("day_count", [1, 2])
def test_eink_rain_amount_and_unit_are_drawn_in_full_inside_each_cell(monkeypatch, rain_mm, day_count):
    from PIL import Image, ImageDraw
    from skoleklar import dashboard_renderer as renderer

    state = weather_state("Museum")
    state["weather"]["days"] = state["weather"]["days"][:day_count]
    for day in state["weather"]["days"]:
        for cell in day["cells"]:
            cell["rain_mm"] = rain_mm
    image = Image.new("RGB", (800, 480), renderer.PAPER)
    draw = ImageDraw.Draw(image)
    drawn = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        drawn.append((text, self.textbbox(xy, text, font=kwargs.get("font"))))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    renderer.draw_weather_panel(draw, state)
    boxes = [box for text, box in drawn if text == f"{rain_mm} mm"]
    assert len(boxes) == day_count * 2  # Any ellipsis or missing unit fails this check.
    first = 368 if day_count == 2 else 471
    for index, (left, top, right, bottom) in enumerate(boxes):
        cell_left = first + (index // 2) * 207 + (index % 2) * 98
        assert cell_left <= left < right <= cell_left + 92
        assert 210 < top < bottom < 248  # Below the glyph and above the divider.


def test_weekend_without_activities_says_so():
    state = weather_state()
    state["weekend"] = []
    assert '<p class="wk-none">Ingen registrerede aktiviteter</p>' in render_html(state)
    assert len(render_png(state).getcolors()) <= 6


def test_sunday_shows_sunday_and_monday_but_judges_only_the_weekend():
    sunday = at("2026-10-04T05:45:00")
    # Saturday dry, Sunday dry, Monday rain all day.
    three = forecast(*SCENARIOS["Udedag"], SCENARIOS["Indedag"][0])
    result = weather.build_weather(three, weather.weekend_dates(sunday), CONFIG, sunday)
    assert [day["day"] for day in result["days"]] == ["Søn. 4. okt.", "Man. 5. okt."]
    assert result["verdict"] == "Udedag" and "man." not in result.get("note", "")
    state = weather.attach_weather(weekday_grid_state("2026-10-04T05:45:00+02:00"), sunday,
                                   config=CONFIG, fetch=lambda _config, _days: three)
    page = render_html(state)
    assert "<h1>Søndag 4. okt.</h1>" in page and ">Mandag</h3>" in page
    assert page.count('class="wx-day"') == 2 and len(render_png(state).getcolors()) <= 6
    # A forecast that stops at Sunday still gives the weekend panel.
    short = weather.build_weather(forecast(*SCENARIOS["Udedag"]), weather.weekend_dates(sunday), CONFIG, sunday)
    assert [day["day"] for day in short["days"]] == ["Søn. 4. okt."]


def test_sun_arc_runs_from_sunrise_to_sunset_and_merges_hours():
    from skoleklar.dashboard_renderer import sun_arc
    day = weather_state("Blandet")["weather"]["days"][0]
    runs, window = sun_arc(day)
    assert runs[0][0] == 0 and runs[-1][1] == 1
    assert all(a[1] == b[0] and a[2] != b[2] for a, b in zip(runs, runs[1:]))
    assert 0 < window[0] < window[1] < 1
    rain = weather_state("Indedag")
    assert sun_arc(rain["weather"]["days"][0])[1] is None
    assert "Ingen tør tur" in render_html(rain) and len(render_png(rain).getcolors()) <= 6


def test_weekend_cover_is_the_day_at_hand():
    from skoleklar.dashboard_renderer import weekend_day_title, weekend_state_label
    saturday = weather_state("Blandet")
    day = saturday["weather"]["days"][0]
    assert weekend_day_title(day) == "Lørdag 3. okt." and weekend_state_label(saturday, day) == "Weekend"
    assert day["icon"] in ("rain", "partly", "cloud") and day["low"] <= day["high"]
    # Friday evening and Saturday evening look ahead to the next day.
    saturday["freshness"]["composed_at"] = "2026-10-02T19:30:00+02:00"
    assert weekend_state_label(saturday, day) == "Weekend · i morgen"
    assert weekend_day_title({"date": "2026-11-01"}) == "Søndag 1. nov."


def test_weekend_inbox_shows_each_childs_latest_message_and_the_total():
    state = weather_state("Blandet")
    left = render_html(state).split('<section class="weather">')[0]
    # As on the parents' board: the child, the sender's first name, the date; then the total.
    assert '<b>ChildA</b> · Teacher</span>' in left and '7 ulæste i alt</p>' in left
    assert "Teacher T." not in left
    read = weather_state("Blandet")
    read["messages"]["items"][0]["unread"] = not state["messages"]["items"][0].get("unread")
    # A filled envelope means unread, an outline that it is read.
    assert render_png(state).tobytes() != render_png(read).tobytes()


def test_one_day_layout_on_sunday():
    sunday = at("2026-10-04T05:45:00")
    state = weather.attach_weather(
        weekday_grid_state("2026-10-04T05:45:00+02:00"), sunday, config=CONFIG,
        fetch=lambda _config, days: forecast(("c",) + SCENARIOS["Udedag"][0][1:], SCENARIOS["Udedag"][1]))
    assert [day["date"] for day in state["weather"]["days"]] == ["2026-10-04"]
    assert render_html(state).count('class="wx-day"') == 1
    assert len(render_png(state).getcolors()) <= 6


@pytest.mark.parametrize("change", [
    lambda w: w.update(verdict="Solskin"),
    lambda w: w.update(extra="x"),
    lambda w: w.update(reason="x" * 91),
    lambda w: w.update(days=[]),
    lambda w: w["days"][0].update(date="2026-10-05"),          # Monday may only come second
    lambda w: w["days"][1].update(date="2026-10-06"),          # a Tuesday
    lambda w: w["days"][0].update(hours="scr"),
    lambda w: w["days"][0].update(sunrise="7:16"),
    lambda w: w["days"][0].update(rain_mm=-1),
    lambda w: w["days"][0].update(rain_mm=0.5),
    lambda w: w["days"][0]["cells"][0].update(icon="tornado"),
    lambda w: w["days"][0]["cells"][0].update(temp=15.5),
    lambda w: w["days"][0]["cells"][0].update(source_url="https://example.invalid"),
    lambda w: w["days"][0].update(flags=[{"icon": "uv", "label": "UV 5"}] * 3),
    lambda w: w["days"][0].update(window={"start": 14, "end": 12, "label": "Tør"}),
    lambda w: w["days"][0].update(window={"start": 4, "end": 8, "label": "Tør"}),
])
def test_renderer_allowlists_the_weather_object(change):
    state = copy.deepcopy(weather_state())
    validate(state)
    change(state["weather"])
    with pytest.raises((ValueError, KeyError)):
        validate(state)


def test_cloud_job_keeps_the_dashboard_when_the_forecast_fails(monkeypatch):
    events = []
    monkeypatch.setattr(scan_service, "_event", lambda name, **fields: events.append((name, fields)))
    state = weekday_grid_state("2026-10-03T05:45:00+02:00")
    museum = weather_state("Museum")

    def boom(*_args, **_kwargs):
        raise TimeoutError("forecast provider timed out")

    monkeypatch.setattr(weather, "attach_weather", boom)
    kept = scan_service._with_weekend_weather(state, SATURDAY_MORNING)
    # The board stays as it was, and says that the weather is missing.
    assert {**kept, "health": None} == {**state, "health": None}
    assert kept["health"] == {"weather": {"status": "degraded", "reason": "timeout_error"}}
    assert events == [("weather_failed", {"error_type": "TimeoutError"})]

    events.clear()
    monkeypatch.setattr(weather, "attach_weather", lambda value, _now: museum)
    assert scan_service._with_weekend_weather(state, SATURDAY_MORNING)["weather"]["verdict"] == "Museum"
    assert events == [("weather_attached", {"verdict": "Museum", "day_count": 2})]
