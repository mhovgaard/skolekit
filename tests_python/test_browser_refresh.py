"""The landing-page refresh works with plain HTML, including recovery paths."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import pytest

from skoleklar.browser_api import create_app, REFRESH_COOKIE
from tests_python.test_display_services import fixture
from tests_python.test_refresh_control import Dispatcher, MemoryStore, RefreshController


OWNER = {"X-Goog-IAP-JWT-Assertion": "owner"}


@pytest.fixture
def browser(tmp_path):
    _, displays, bundle = fixture(tmp_path)
    dispatcher = Dispatcher()
    controller = RefreshController(MemoryStore(), dispatcher)
    app = create_app(store=displays, controller=controller,
                     verifier=lambda token, audience: {"email": token + "@example.com"},
                     audience="test", owners=["owner@example.com"], viewers=["viewer@example.com"],
                     origin="https://testserver")
    return TestClient(app, base_url="https://testserver"), dispatcher, controller, displays, bundle


def form(page):
    fields = BeautifulSoup(page.text, "html.parser").select("form input")
    return {field["name"]: field["value"] for field in fields}


def submit(client, fields):
    return client.post("/admin/refresh", data=fields, headers={**OWNER, "Origin": "https://testserver"},
                       follow_redirects=False)


def test_click_tracks_one_run_then_shows_the_new_dashboard_and_stops(browser):
    client, dispatcher, _, displays, bundle = browser
    page = client.get("/", headers=OWNER)
    assert page.status_code == 200
    assert page.text.index("Opdater nu") < page.text.index('class="frame"')
    assert "http-equiv='refresh'" not in page.text
    assert dispatcher.calls == 0
    fields = form(page)
    response = submit(client, fields)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert client.cookies.get(REFRESH_COOKIE) == fields["request_id"]
    assert submit(client, fields).status_code == 303  # Double click cannot start another job.
    for _ in range(2):
        page = client.get("/", headers=OWNER)
        soup = BeautifulSoup(page.text, "html.parser")
        assert soup.select_one("progress").get("value") is None
        assert soup.select_one("button").has_attr("disabled")
        assert not soup.select("form")
        assert "content='5;url=/'" in page.text
    assert dispatcher.calls == 1
    bundle["html"] = bundle["html"].replace("</body>", "<p>Nyeste publicering</p></body>")
    displays.write_json("current.json", bundle)
    dispatcher.state = "complete"
    page = client.get("/", headers=OWNER)
    assert "Opdateringen er færdig" in page.text and "Nyeste publicering" in page.text
    assert "http-equiv='refresh'" not in page.text
    assert BeautifulSoup(page.text, "html.parser").select_one("progress")["value"] == "1"
    assert REFRESH_COOKIE not in client.cookies
    assert form(page)["request_id"] != fields["request_id"]
    assert client.get("/", headers=OWNER).status_code == 200 and dispatcher.calls == 1


def test_expired_dashboard_still_has_a_refresh_button_and_tracker(browser):
    client, dispatcher, _, displays, bundle = browser
    bundle["metadata"]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    displays.write_json("current.json", bundle)
    page = client.get("/", headers=OWNER)
    assert page.status_code == 503 and "Opdater nu" in page.text
    assert "Skoleoverblik utilgængeligt" in page.text
    submit(client, form(page))
    page = client.get("/", headers=OWNER)
    assert "content='5;url=/'" in page.text and dispatcher.calls == 1


def test_viewer_never_gets_refresh_controls_or_auto_checks(browser):
    client, dispatcher, _, _, _ = browser
    client.cookies.set(REFRESH_COOKIE, str(uuid4()))
    page = client.get("/", headers={"X-Goog-IAP-JWT-Assertion": "viewer"})
    assert page.status_code == 200
    assert "Opdater nu" not in page.text and "http-equiv='refresh'" not in page.text
    assert "ag_csrf" not in page.headers.get("set-cookie", "")
    assert dispatcher.calls == 0


def test_failure_stops_tracking_and_offers_an_explicit_retry(browser):
    client, dispatcher, _, _, _ = browser
    submit(client, form(client.get("/", headers=OWNER)))
    dispatcher.state = "failed"
    page = client.get("/", headers=OWNER)
    assert "Opdateringen mislykkedes" in page.text and form(page)
    assert "http-equiv='refresh'" not in page.text and "Opdateringen er færdig" not in page.text
    assert REFRESH_COOKIE not in client.cookies and dispatcher.calls == 1
    submit(client, form(page))
    assert dispatcher.calls == 2


def test_uncertain_dispatch_never_retries_or_pretends_to_complete(browser):
    client, dispatcher, _, _, _ = browser
    fields = form(client.get("/", headers=OWNER))
    dispatcher.fail = True
    submit(client, fields)
    for _ in range(2):
        page = client.get("/", headers=OWNER)
        assert "Starten kunne ikke bekræftes" in page.text
        assert "Kontrollér status" in page.text
        assert "http-equiv='refresh'" not in page.text and not form(page)
        assert "private provider response" not in page.text
    assert dispatcher.calls == 1 and client.cookies.get(REFRESH_COOKIE) == fields["request_id"]


def test_temporary_status_failure_keeps_the_request_for_manual_recheck(browser, monkeypatch):
    client, dispatcher, _, _, _ = browser
    fields = form(client.get("/", headers=OWNER))
    submit(client, fields)
    def unavailable(_):
        raise TimeoutError("private error")
    monkeypatch.setattr(dispatcher, "status", unavailable)
    page = client.get("/", headers=OWNER)
    assert "Status kunne ikke hentes" in page.text and not form(page)
    assert "http-equiv='refresh'" not in page.text and "private error" not in page.text
    assert client.cookies.get(REFRESH_COOKIE) == fields["request_id"]


def test_long_running_refresh_stops_auto_checks_without_starting_another(browser):
    client, dispatcher, controller, _, _ = browser
    fields = form(client.get("/", headers=OWNER))
    submit(client, fields)
    name = controller._name("browser:owner@example.com", fields["request_id"])
    record, generation = controller.store.read(name)
    record["requested_at"] = (datetime.now(UTC) - timedelta(minutes=36)).isoformat()
    controller.store.put(name, record, generation)
    page = client.get("/", headers=OWNER)
    assert "længere tid end forventet" in page.text
    assert "http-equiv='refresh'" not in page.text and not form(page)
    assert dispatcher.calls == 1
    dispatcher.state = "complete"
    assert "Opdateringen er færdig" in client.get("/", headers=OWNER).text


def test_busy_other_request_shows_readable_message_without_duplicate_dispatch(browser):
    client, dispatcher, controller, _, _ = browser
    fields = form(client.get("/", headers=OWNER))
    controller.trigger("another-device", str(uuid4()))
    response = submit(client, fields)
    page = client.get(response.headers["location"], headers=OWNER)
    assert "En opdatering er allerede i gang" in page.text
    assert "http-equiv='refresh'" not in page.text and dispatcher.calls == 1


def test_tracking_cookie_is_scoped_to_the_owner(browser):
    client, dispatcher, controller, _, _ = browser
    identifier = str(uuid4())
    controller.trigger("another-owner", identifier)
    client.cookies.set(REFRESH_COOKIE, identifier, domain="testserver.local", path="/")
    page = client.get("/", headers=OWNER)
    assert "Opdateringen kunne ikke findes" in page.text
    assert "http-equiv='refresh'" not in page.text and dispatcher.calls == 1


def test_main_page_form_preserves_csrf_across_tabs_and_rejects_forgery(browser):
    client, dispatcher, _, _, _ = browser
    first = form(client.get("/", headers=OWNER))
    second = form(client.get("/", headers=OWNER))
    assert first["csrf"] == second["csrf"]
    assert submit(client, {**first, "csrf": "wrong"}).status_code == 400
    assert client.post("/admin/refresh", data=first,
                       headers={**OWNER, "Origin": "https://other.example"}).status_code == 403
    assert dispatcher.calls == 0
    assert submit(client, first).status_code == 303 and dispatcher.calls == 1


def test_old_control_link_redirects_to_the_landing_page(browser):
    client, dispatcher, _, _, _ = browser
    response = client.get("/control", headers=OWNER, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert dispatcher.calls == 0


def test_native_browser_form_tracks_completion_without_javascript(browser, tmp_path):
    """Exercise real cookies, redirects and meta refresh at desktop and phone widths."""
    import socket
    from threading import Thread
    import uvicorn
    from playwright.sync_api import sync_playwright
    from tests_python.test_dashboard_browser_layout import CHROME

    if CHROME is None:
        pytest.skip("Chrome/Chromium not available")
    _, dispatcher, controller, displays, bundle = browser
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://localhost:{listener.getsockname()[1]}"
    app = create_app(store=displays, controller=controller,
                     verifier=lambda token, audience: {"email": "owner@example.com"},
                     audience="test", owners=["owner@example.com"], origin=origin)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            chrome = playwright.chromium.launch(executable_path=CHROME, headless=True)
            context = chrome.new_context(java_script_enabled=False, viewport={"width": 1000, "height": 800})
            page = context.new_page()
            page.goto(origin)
            page.screenshot(path=str(tmp_path / "refresh-idle.png"), full_page=True)
            assert dispatcher.calls == 0
            page.get_by_role("button", name="Opdater nu").click()
            page.wait_for_selector(".refresh-status[data-state=running]")
            assert page.get_by_role("button", name="Opdaterer…").is_disabled()
            page.screenshot(path=str(tmp_path / "refresh-running.png"), full_page=True)
            bundle["html"] = bundle["html"].replace("</body>", "<p>Nyeste publicering</p></body>")
            displays.write_json("current.json", bundle)
            dispatcher.state = "complete"
            page.wait_for_selector(".refresh-status[data-state=complete]", timeout=15000)
            assert page.get_by_text("Nyeste publicering").is_visible()
            assert page.locator("meta[http-equiv=refresh]").count() == 0
            assert dispatcher.calls == 1
            assert not any(cookie["name"] == REFRESH_COOKIE for cookie in context.cookies())
            page.screenshot(path=str(tmp_path / "refresh-complete.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(tmp_path / "refresh-mobile.png"), full_page=True)
            bar = page.locator(".browser-bar").bounding_box()
            button = page.get_by_role("button", name="Opdater nu").bounding_box()
            links = page.locator(".browser-links").bounding_box()
            assert bar["x"] >= 0 and bar["x"] + bar["width"] <= 390
            for box in (button, links):
                assert bar["x"] <= box["x"] and box["x"] + box["width"] <= bar["x"] + bar["width"]
            chrome.close()
    finally:
        server.should_exit = True
        worker.join(timeout=5)
        listener.close()
