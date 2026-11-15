"""Phase 2: every request to the school goes through one read-only client.

The client reaches only the profile's host, sends GET plus POST to known read
endpoints, and raises one `SessionExpired` on a login redirect. The tests here
fail when a module builds its own client for the school, or when anything
POSTs outside the allowlist.
"""
import json
import os
import re
import stat
from pathlib import Path

import httpx
import pytest

from skolekit import state as settings
from skolekit import transport
from skolekit.auth import credentials, session
from skolekit.sources import calendar as calendar_source
from skoleklar.harvest import collect_all
from skolekit.transport import READ_POSTS, ReadOnlyClient, ReadOnlyViolation, SessionExpired
from tests_python import test_collect_all_golden as golden
from tests_python.skoleintra_site import CHILDREN, SCHOOL, SyntheticSite, frozen_datetime

SRC = Path(__file__).resolve().parents[1] / "src"
HOST = SCHOOL.host

# Modules allowed to build an HTTP client, and what each one talks to. None of
# them reaches SkoleIntra; adding one here is a reviewed decision.
OTHER_SERVICES = {
    "skolekit/transport.py": "the read-only client itself",
    "skoleklar/triage/derivation.py": "the model provider (OpenRouter)",
    "skoleklar/triage/message_events.py": "the model provider (OpenRouter)",
    "skoleklar/weather.py": "the weather forecast",
    "skoleklar/refresh_control.py": "the Cloud Run API",
    "skoleklar/device_client.py": "the household's own delivery gateway",
}
_BUILDS_CLIENT = re.compile(
    r"httpx\.(?:Client|AsyncClient)\s*\(|httpx\.(?:get|post|put|patch|delete|request|stream)\s*\("
    r"|class \w+\(httpx\.(?:Client|AsyncClient)\)")


# --- No module builds its own client for the school ----------------------------------------

def test_only_the_listed_modules_build_an_http_client():
    builders = sorted(path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py")
                      if path.name != "__init__.py" and _BUILDS_CLIENT.search(path.read_text(encoding="utf-8")))
    assert builders == sorted(OTHER_SERVICES)


def test_no_listed_module_names_the_school():
    for name in set(OTHER_SERVICES) - {"skolekit/transport.py"}:
        text = (SRC / name).read_text(encoding="utf-8")
        # No school host, no child route, no school session.
        assert not re.search(r"skoleintra\.dk|\.route\(|weekly_route\(|school_client|auth-state", text), name


def test_the_post_allowlist_is_only_the_front_pages_show_more():
    # Widening this is a change to what the toolkit may do on the school's site.
    assert READ_POSTS == frozenset({"newsitems/showmore"})


# --- What the client refuses ---------------------------------------------------------------

class Site:
    def __init__(self, responder=None):
        self.requests: list[httpx.Request] = []
        self.responder = responder or (lambda request: httpx.Response(200, text="ok"))

    def __call__(self, request):
        self.requests.append(request)
        return self.responder(request)

    def client(self, host=HOST):
        return ReadOnlyClient(host, transport=httpx.MockTransport(self))


def test_a_get_on_the_school_host_goes_through_with_the_headers():
    site = Site()
    with site.client() as client:
        assert client.get(f"https://{HOST}/parent/1/A/Index").text == "ok"
    [request] = site.requests
    assert request.headers["User-Agent"] == "skolekit/0.1 (read-only)"
    assert request.headers["Accept-Language"].startswith("da-DK")


@pytest.mark.parametrize("method, url", [
    ("GET", "https://other.m.skoleintra.dk/parent/1/A/Index"),
    ("GET", "https://example.com/"),
    ("GET", f"http://{HOST}/parent/1/A/Index"),
    ("POST", f"https://{HOST}/parent/1/A/forms/open/survey/501/answer"),
    ("POST", f"https://{HOST}/parent/1/A/messages/conversations/send"),
    ("POST", f"https://{HOST}/parent/1/A/calendareventsource/viewchanged"),
    ("PUT", f"https://{HOST}/parent/1/A/Index"),
    ("DELETE", f"https://{HOST}/parent/1/A/Index"),
])
def test_writes_and_other_hosts_are_refused_before_anything_is_sent(method, url):
    site = Site()
    with site.client() as client, pytest.raises(ReadOnlyViolation):
        client.request(method, url)
    assert site.requests == []


def test_the_front_pages_show_more_is_the_one_post():
    site = Site(lambda request: httpx.Response(200, json={"View": ""}))
    with site.client() as client:
        client.post(f"https://{HOST}/parent/1/A/newsitems/showmore", data={"pagings": "{}"})
    assert [request.method for request in site.requests] == ["POST"]


def test_a_client_for_another_site_cant_be_built():
    with pytest.raises(ReadOnlyViolation):
        ReadOnlyClient("example.com")


@pytest.mark.parametrize("location", [
    "/Account/IdpLogin?ReturnUrl=%2Fparent",
    "https://broker.unilogin.dk/auth/realms/broker/protocol/openid-connect/auth",
])
def test_a_redirect_to_a_login_raises_session_expired_without_following_it(location):
    site = Site(lambda request: httpx.Response(302, headers={"location": location}))
    with site.client() as client, pytest.raises(SessionExpired, match="session has expired; sign in again"):
        client.get(f"https://{HOST}/parent/1/A/Index")
    assert [request.url.path for request in site.requests] == ["/parent/1/A/Index"]


def test_a_redirect_within_the_school_is_followed():
    def responder(request):
        if request.url.path == "/parent/1/A":
            return httpx.Response(302, headers={"location": "/parent/1/A/Index"})
        return httpx.Response(200, text="index")
    site = Site(responder)
    with site.client() as client:
        assert client.get(f"https://{HOST}/parent/1/A").text == "index"


# --- The synthetic site through the client -------------------------------------------------

@pytest.mark.parametrize("name", sorted(golden.POLICIES))
def test_the_harvest_through_the_client_matches_the_golden_file(monkeypatch, name):
    # The read-only client lets through every request today's harvest makes,
    # the show-more POST included, and changes nothing in what it collects.
    monkeypatch.setattr(calendar_source, "datetime", frozen_datetime())
    monkeypatch.setattr(SyntheticSite, "client",
                        lambda site: ReadOnlyClient(HOST, transport=httpx.MockTransport(site.handle)))
    actual = golden.harvest_document(golden.POLICIES[name])
    expected = json.loads((golden.GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    assert actual == expected


def test_an_expired_session_is_one_error_for_each_source(monkeypatch):
    monkeypatch.setattr(calendar_source, "datetime", frozen_datetime())
    site = SyntheticSite()
    site.redirect_to_login(CHILDREN[0], "Index")
    site.redirect_to_login(CHILDREN[0], "forms/open")
    with ReadOnlyClient(HOST, transport=httpx.MockTransport(site.handle)) as client:
        checks = collect_all(client, children=CHILDREN[:1])
    errors = {check.source_type: check.error for check in checks if check.error}
    expired = "SessionExpired: the school's session has expired; sign in again"
    assert errors == {"frontpage": expired, "forms": expired}


def test_the_restricted_inbox_reports_an_expired_session_not_a_parse_error():
    from skoleklar.restricted_messages import collect_restricted_messages

    site = SyntheticSite()
    site.redirect_to_login(CHILDREN[0], "messages/conversations")
    with ReadOnlyClient(HOST, transport=httpx.MockTransport(site.handle)) as client, \
            pytest.raises(SessionExpired):
        collect_restricted_messages(client, CHILDREN[0], key=b"k" * 32, previous_states={},
                                    page_limit=1, page_safety_limit=10, fetch_changed_content=False)


# --- The session, the credentials and the state directory ----------------------------------

def test_the_session_store_keeps_the_cookies_private(tmp_path):
    store = session.SessionStore(tmp_path / "home")
    assert store.load() is None
    store.save({"cookies": [{"name": "a", "value": "1", "domain": f".{HOST}"}]})
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.load()["cookies"][0]["name"] == "a"


def test_only_skoleintra_cookies_reach_the_client():
    jar = session.cookies_from_state({"cookies": [
        {"name": "school", "value": "1", "domain": f".{HOST}"},
        {"name": "root", "value": "2", "domain": "skoleintra.dk"},
        {"name": "idp", "value": "3", "domain": "broker.unilogin.dk"},
        {"name": "none", "value": "4"},
    ]})
    assert sorted(cookie.name for cookie in jar.jar) == ["root", "school"]


def test_the_environment_login_comes_before_the_keychain(monkeypatch):
    class Keychain:
        def school_login(self):
            return ("keychain-user", "keychain-secret")

    chain = credentials.ChainedCredentials((credentials.EnvironmentCredentials(), Keychain()))
    monkeypatch.delenv("AG_SCHOOL_USERNAME", raising=False)
    monkeypatch.delenv("AG_SCHOOL_PASSWORD", raising=False)
    assert chain.school_login() == ("keychain-user", "keychain-secret")
    monkeypatch.setenv("AG_SCHOOL_USERNAME", "env-user")
    monkeypatch.setenv("AG_SCHOOL_PASSWORD", "env-secret")
    assert chain.school_login() == ("env-user", "env-secret")


def test_a_run_points_at_its_own_state_dir_and_leaves_the_environment_alone(tmp_path):
    before = os.environ["AG_SCHOOL_HOME"]
    with settings.using_state_dir(tmp_path / "run") as path:
        assert settings.state_dir() == path == tmp_path / "run"
        assert session.SessionStore().path == tmp_path / "run" / "auth-state.json"
        assert os.environ["AG_SCHOOL_HOME"] == before
    assert settings.state_dir() == Path(before).resolve()


def test_the_keychain_service_is_named_once():
    # The toolkit's own name is skolekit; the product's Keychain name is its setting.
    named = [path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py")
             if '"ag-skoleintra"' in path.read_text(encoding="utf-8")]
    assert named == ["skoleklar/settings.py"]
    assert transport.ALLOWED_HOST_SUFFIX == ".skoleintra.dk"
