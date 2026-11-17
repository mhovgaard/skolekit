"""Measure the rendered dashboard in a real browser.

These tests assert on what the browser actually laid out: nothing overflows,
everything the parents' board decided to show is visible, and the child rows
line up across the dark and light halves.
"""
import base64
import hashlib
from pathlib import Path
import shutil

import pytest

from tests_python.board_lines import line
from tests_python.test_dashboard_rendering import dashboard, weekday_grid_state

CHROME = next(
    (
        path
        for path in (
            shutil.which("google-chrome"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        if path and Path(path).exists()
    ),
    None,
)
pytestmark = pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not available")

PROBE = """() => {
const all=s=>[...document.querySelectorAll(s)], r=e=>e.getBoundingClientRect();
const panel=document.querySelector('.dashboard'), top=r(panel).top;
return {
  panel:[Math.round(r(panel).width),Math.round(r(panel).height)],
  clipped:all('.dashboard *').filter(e=>{const c=getComputedStyle(e);
    if(c.display==='contents'||c.display==='inline')return false;
    return (e.clientHeight>0&&e.scrollHeight>e.clientHeight+1)||(e.clientWidth>0&&e.scrollWidth>e.clientWidth+1);
  }).map(e=>e.tagName+'.'+e.className),
  text:panel.innerText,
  leftTops:all('.pb-left,.pb-begge-l').map(e=>Math.round(r(e).top-top)),
  rightTops:all('.pb-right,.pb-begge-r').map(e=>Math.round(r(e).top-top)),
  bottom:Math.round(Math.max(...all('.dashboard *').map(e=>r(e).bottom))-top),
  fontLoaded:document.fonts.check('700 12px "Newsreader Embedded"'),
  boldDates:all('.pb-msg.unread time').map(e=>getComputedStyle(e).fontWeight),
};
}"""


def measure(page: str, *, width: int = 900, font: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    if font:
        page = page.replace("--serif:", f"--serif:{font},", 1).replace("--sans:", f"--sans:{font},", 1)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        view = browser.new_page(viewport={"width": width, "height": 1600})
        view.set_content(page, wait_until="load")
        view.evaluate("document.fonts.ready")
        result = view.evaluate(PROBE)
        browser.close()
    return result


def shown_texts(value, live_messages=None):
    """Every text the parents' board decided to show, as the browser renders it."""
    from skoleklar import parents_board_render as pb

    model, _ = pb.layout(value, live_messages)
    texts = [kid.name for kid in model.kids]
    for kid in model.kids:
        texts += ["".join(text for text, _ in item.runs).strip() for item in kid.left]
        for _, items, _ in kid.days:
            texts += ["".join(text for text, _ in item.runs).strip() for item in items]
    return [text for text in texts if text]


def busy_state():
    value = weekday_grid_state()
    for day in value["weekdays"][:3]:
        iso = day["date"]
        day["children"][1]["lines"] = [line("bamse, hvis du er ugens kammerat", "bring", day=iso),
                                       line("turtaske og drikkedunk", "optional", day=iso),
                                       line("Alexander W.-U. · KULTUR 00", "substitute", day=iso)]
        day["children"][0]["lines"] = [line("Læs side 4", "homework", day=iso)]
    hero = value["hero"]["date"]
    value["hero"]["children"][0]["lines"] = [line("Elisabeth fejrer sin fødselsdag", day=hero),
                                             line("Tur til søen i kulturfag", "trip", day=hero)]
    return value


def live_rows(value):
    from dataclasses import replace

    from skoleklar.live_subjects import DisplayMessage

    names = [child["name"] for child in value["hero"]["children"]]
    return [
        replace(DisplayMessage(names[0], "Anna K.", "Invitation til klassens høstfest", "safe",
                               "28/09 15:33", True), topic="Høstfest"),
        DisplayMessage(names[1], "Peter L.", "Ugeplan og praktisk information", "safe", "28/09 19:53", False),
    ]


@pytest.mark.parametrize("width", [900, 375])
def test_weekend_artwork_loads_and_leaves_activities_and_inbox_visible(tmp_path, width):
    from playwright.sync_api import sync_playwright
    from tests_python.test_weather import weather_state

    state = weather_state("Museum")
    state["weekend"] = [
        {"date": "2026-10-03", "day": "Lør. 3. okt.", "who": "Begge",
         "lines": [line("Tur i skoven kl. 11", "trip", day="2026-10-03")]},
        {"date": "2026-10-04", "day": "Søn. 4. okt.", "who": "ChildA",
         "lines": [line("Besøg hos familien kl. 14", "event", day="2026-10-04")]},
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": width, "height": 1200})
        page.set_content(dashboard.render_html(state), wait_until="load")
        page.evaluate("document.fonts.ready")
        result = page.evaluate("""() => {
            const panel=document.querySelector('.dashboard'),r=e=>e.getBoundingClientRect();
            const image=document.querySelector('.wx-cover img');
            return {
                clipped:[...panel.querySelectorAll('*')].filter(e=>{
                    const s=getComputedStyle(e);
                    return s.display!=='contents'&&s.display!=='inline'&&(
                        (e.clientWidth>0&&e.scrollWidth>e.clientWidth+1)||
                        (e.clientHeight>0&&e.scrollHeight>e.clientHeight+1));
                }).map(e=>e.tagName+'.'+e.className),
                artwork:[{loaded:image.complete&&image.naturalWidth>0,bottom:r(image).bottom}],
                msgTop:r(document.querySelector('.messages')).top,text:panel.innerText,
                panel:[r(panel).width,r(panel).bottom],
                bottom:Math.max(...[...panel.querySelectorAll('*')].map(e=>r(e).bottom)),
                documentWidth:document.documentElement.scrollWidth,viewport:innerWidth
            };
        }""")
        browser.close()
    assert result["clipped"] == []
    assert len(result["artwork"]) == 1 and result["artwork"][0]["loaded"]
    assert result["artwork"][0]["bottom"] < result["msgTop"]
    assert "Tur i skoven" in result["text"] and "Besøg hos familien" in result["text"]
    assert "7 ulæste i alt" in result["text"]
    assert result["bottom"] <= result["panel"][1] + 1
    assert result["documentWidth"] <= result["viewport"]


@pytest.mark.parametrize("width", [900, 500])
@pytest.mark.parametrize("font", [None, "'Courier New'"])
def test_nothing_is_clipped_and_every_item_is_visible(width, font):
    """Even with a much wider font the page grows instead of hiding text."""
    value = busy_state()
    result = measure(dashboard.render_html(value), width=width, font=font)
    assert result["clipped"] == []
    flattened = " ".join(result["text"].split())
    for text in shown_texts(value):
        assert " ".join(text.split()) in flattened, text


def test_default_font_fits_the_800_by_480_panel():
    result = measure(dashboard.render_html(weekday_grid_state()))
    assert result["panel"] == [800, 480]
    assert result["bottom"] <= 480


def test_child_rows_line_up_across_the_split():
    result = measure(dashboard.render_html(busy_state()))
    assert len(result["leftTops"]) == 3
    assert result["leftTops"] == result["rightTops"]


def test_message_lines_show_sender_topic_and_unread_date():
    value = weekday_grid_state()
    result = measure(dashboard.render_html(value, live_messages=live_rows(value)))
    flattened = " ".join(result["text"].split())
    assert "Anna: Høstfest 28/09" in flattened
    # No topic for the second child: the sender and the date only, never the subject.
    assert "Peter 28/09" in flattened and "Ugeplan" not in flattened
    assert result["boldDates"] == ["700"]
    assert result["clipped"] == []


def test_script_is_constant_and_matches_csp_hash():
    page = dashboard.render_html(busy_state())
    script = page.split("<script>")[1].split("</script>")[0]
    digest = "sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert digest == dashboard.FOLD_SCRIPT_CSP_HASH


def test_embedded_font_loads_and_covers_danish():
    assert measure(dashboard.render_html(weekday_grid_state()))["fontLoaded"] is True


def test_font_data_is_not_corrupted_by_css_unit_rewrite():
    page = dashboard.render_html(weekday_grid_state())
    from skoleklar import dashboard_renderer

    assert dashboard_renderer._font_faces() in page
    assert "calc(" not in dashboard_renderer._font_faces()
