"""A synthetic SkoleIntra site served through `httpx.MockTransport`.

The routes, HTML and JSON shapes are the ones the collectors in
`skolekit.sources` read (see docs/data-sources.md). Every value is invented: the two children, their
IDs and classes, the staff, the notices, the plans, the events and the messages.
Never copy real school content, children's names or surnames into the fixtures;
`test_site_collectors.py` checks the household's own identifiers stay out.

Fixture files live in `fixtures/skoleintra_site/`, one directory per child,
with shared page templates beside them. `{{student_id}}` and `{{child}}` are
filled per child, and `{{payload}}` with a sibling JSON file, HTML-escaped as the
site embeds it in an attribute.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx

from skolekit.profile import Child as ChildConfig
from tests_python.synthetic_household import ALFA, BETA, SCHOOL


FIXTURES = Path(__file__).parent / "fixtures" / "skoleintra_site"
ZONE = ZoneInfo("Europe/Copenhagen")
# Thursday morning in school week 46. The cloud scan's daily window is then
# Monday 2 November to Monday 30 November.
NOW = datetime(2026, 11, 12, 7, 0, tzinfo=ZONE)

# The site serves the invented household (synthetic_household.py): its host and children.
CHILDREN = (ALFA, BETA)

# `/parent/{id}/{child}/{route}`, or `/parent/{id}/{child}item/{route}` for the
# weekly plans, where the site leaves out the slash.
CHILD_PATH = re.compile(
    r"^/parent/(?P<student_id>\d+)/(?P<child>[^/]+?)(?P<item>/|item/)(?P<route>.*)$"
)
FORM_PAGE = re.compile(
    r"^forms/(?P<state>open|closed)/(?P<kind>[^/]+)/(?P<id>[^/]+?)"
    r"(?P<answer>/answer)?(?:/edit)?$"
)
WEEKLY_ITEM = re.compile(r"^item/weeklyplansandhomework/item/class/(?P<week>\d+)-(?P<year>\d+)$")
MESSAGE_ENDPOINT = "messages/conversations/"
READ_ONLY_MESSAGE_ROUTES = {
    "expandthreadmessages",
    "getmessageforthreadlessconversation",
    "getnewconversations",
    "loadmessagesforselectedconversation",
}

Responder = Callable[[httpx.Request], httpx.Response]


def frozen_datetime(now: datetime = NOW) -> type[datetime]:
    """A `datetime` whose `now()` is `now`, for patching a module's import."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            value = now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)
            return cls.combine(value.date(), value.timetz())

    return FrozenDatetime


class SyntheticSite:
    """Serves each child's fixture directory and records every request.

    `override(child, route, responder)` replaces one route, for failures and
    edge cases. Unknown routes answer 404; write endpoints answer 403 and are
    recorded in `forbidden`.
    """

    def __init__(self, children: tuple[ChildConfig, ...] = CHILDREN):
        self.children = {(child.student_id, child.child): child for child in children}
        self.requests: list[httpx.Request] = []
        self.forbidden: list[str] = []
        self.overrides: dict[tuple[str, str], Responder] = {}

    def client(self) -> httpx.Client:
        """A client configured like the harvest's (`cli.command_harvest`)."""
        return httpx.Client(
            transport=httpx.MockTransport(self.handle),
            follow_redirects=True,
            timeout=30,
            headers={
                "Accept-Language": "da-DK,da;q=0.9,en;q=0.7",
                "User-Agent": "AG-SkoleIntra/0.1 read-only monitor",
            },
        )

    def override(self, child: ChildConfig, route: str, responder: Responder) -> None:
        self.overrides[(child.child, route)] = responder

    def redirect_to_login(self, child: ChildConfig, route: str) -> None:
        """The site's answer when the session has expired."""
        self.override(
            child,
            route,
            lambda request: httpx.Response(
                302, headers={"location": "/Account/IdpLogin?ReturnUrl=%2Fparent"}
            ),
        )

    def routes(self, child: ChildConfig | None = None) -> list[tuple[str, str]]:
        """(method, route) of each recorded request, optionally for one child."""
        result = []
        for request in self.requests:
            parsed = self._parse(request)
            if parsed is None:
                result.append((request.method, request.url.path))
            elif child is None or parsed[0] is child:
                result.append((request.method, parsed[1]))
        return result

    def text(self, child: ChildConfig, name: str) -> str:
        path = self._fixture(child, name)
        if path is None:
            raise FileNotFoundError(name)
        return self._fill(path.read_text(encoding="utf-8"), child)

    def json(self, child: ChildConfig, name: str):
        return json.loads(self.text(child, name))

    # Request handling

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if f"{request.url.scheme}://{request.url.host}" != SCHOOL.base_url:
            return httpx.Response(404, text="unknown host")
        if request.url.path.lower().startswith("/account/"):
            return html_page(self._fixture(None, "login.html").read_text(encoding="utf-8"))
        parsed = self._parse(request)
        if parsed is None:
            return httpx.Response(404, text="unknown route")
        child, route = parsed
        override = self.overrides.get((child.child, route))
        if override is not None:
            return override(request)
        try:
            return self._serve(child, route, request)
        except FileNotFoundError:
            return httpx.Response(404, text="no fixture")

    def _parse(self, request: httpx.Request) -> tuple[ChildConfig, str] | None:
        match = CHILD_PATH.match(request.url.path)
        if not match:
            return None
        child = self.children.get((int(match.group("student_id")), match.group("child")))
        if child is None:
            return None
        route = match.group("route")
        if match.group("item") == "item/":
            route = f"item/{route}"
        return child, route

    def _serve(self, child: ChildConfig, route: str, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if route.startswith(MESSAGE_ENDPOINT) and route[len(MESSAGE_ENDPOINT):] not in (
            READ_ONLY_MESSAGE_ROUTES
        ):
            self.forbidden.append(f"{request.method} {route}")
            return httpx.Response(403, text="write endpoint")
        if request.method == "POST":
            if route == "newsitems/showmore":
                return self._show_more(child, request)
            self.forbidden.append(f"POST {route}")
            return httpx.Response(405, text="read-only test site")
        if route == "Index":
            return html_page(self.text(child, "index.html"))
        if route in {"forms/open", "forms/closed"}:
            state = route.split("/")[1]
            return html_page(
                self.text(child, "forms-list.html"),
                payload=self.json(child, f"forms-{state}.json"),
            )
        form = FORM_PAGE.match(route)
        if form:
            suffix = "-answer" if form.group("answer") else ""
            return html_page(self.text(child, f"form-{form.group('kind')}-{form.group('id')}{suffix}.html"))
        if route == "item/weeklyplansandhomework/list":
            return html_page(self.text(child, "weekly-list.html"))
        weekly = WEEKLY_ITEM.match(route)
        if weekly:
            return html_page(
                self.text(child, "weekly-plan.html"),
                payload=self.json(child, f"weekly-plan-{weekly.group('week')}-{weekly.group('year')}.json"),
            )
        if route.startswith("calendareventsource/"):
            return self._calendar(child, route.split("/", 1)[1], params)
        if route == "messages/conversations":
            return html_page(
                self.text(child, "messages-inbox.html"),
                payload=self.json(child, "messages-inbox.json"),
            )
        if route.startswith(MESSAGE_ENDPOINT):
            return self._messages(child, route[len(MESSAGE_ENDPOINT):], params)
        return httpx.Response(404, text="unknown route")

    def _show_more(self, child: ChildConfig, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("utf-8"))
        pagings = json.loads(form["pagings"][0])
        for page in self.json(child, "showmore.json"):
            if page["pagings"] == pagings:
                return httpx.Response(
                    200,
                    json={
                        "View": self.text(child, page["view"]),
                        "HasMoreItems": page["HasMoreItems"],
                    },
                )
        return httpx.Response(400, text="unexpected cursor")

    def _calendar(self, child: ChildConfig, kind: str, params: httpx.QueryParams) -> httpx.Response:
        if params.get("departmentIds") != json.dumps(list(child.department_ids)):
            return httpx.Response(400, text="departmentIds")
        if (kind == "LessonsEvents") != ("className" in params):
            return httpx.Response(400, text="className is for lessons only")
        if kind == "LessonsEvents" and params["className"] != child.calendar_class_name:
            return httpx.Response(200, json=[])
        start = datetime.fromtimestamp(int(params["start"]), ZONE)
        end = datetime.fromtimestamp(int(params["end"]), ZONE)
        events = []
        for event in self.json(child, f"calendar-{kind}.json"):
            event_start = datetime.fromisoformat(event.get("start") or event["startDate"])
            event_end = datetime.fromisoformat(event.get("end") or event["endDate"])
            if event_start.replace(tzinfo=ZONE) < end and event_end.replace(tzinfo=ZONE) > start:
                events.append(event)
        return httpx.Response(200, json=events)

    def _messages(self, child: ChildConfig, endpoint: str, params: httpx.QueryParams) -> httpx.Response:
        if endpoint == "getnewconversations":
            pages = self.json(child, "messages-pages.json")
            return httpx.Response(200, json=pages.get(params["takeFromRootMessageId"], []))
        if endpoint == "getmessageforthreadlessconversation":
            single = self.json(child, "messages-threadless.json")
            return httpx.Response(200, json=single[params["messageId"]])
        threads = self.json(child, "messages-threads.json")
        thread = threads.get(params["threadId"])
        if thread is None:
            return httpx.Response(404, text="unknown thread")
        if endpoint == "expandthreadmessages":
            return httpx.Response(200, json=thread["headers"])
        headers = thread["headers"]
        if (
            params["takeFromRootMessageId"] != str(headers[0]["MessageId"])
            or params["takeToMessageId"] != str(headers[-1]["MessageId"])
        ):
            return httpx.Response(400, text="unexpected message range")
        return httpx.Response(200, json=thread["messages"])

    # Fixture files

    def _fixture(self, child: ChildConfig | None, name: str) -> Path | None:
        directories = [FIXTURES / child.child.lower()] if child else []
        for directory in [*directories, FIXTURES]:
            path = directory / name
            if path.is_file():
                return path
        return None

    @staticmethod
    def _fill(text: str, child: ChildConfig) -> str:
        return text.replace("{{student_id}}", str(child.student_id)).replace(
            "{{child}}", child.child
        )


def html_page(text: str, *, payload=None) -> httpx.Response:
    """An HTML response; `{{payload}}` becomes the JSON, escaped for an attribute."""
    if payload is not None:
        encoded = html.escape(json.dumps(payload, ensure_ascii=False), quote=True)
        text = text.replace("{{payload}}", encoded)
    return httpx.Response(200, text=text, headers={"content-type": "text/html; charset=utf-8"})


def json_response(value, status: int = 200) -> Responder:
    return lambda request: httpx.Response(status, json=value)


def status_response(status: int) -> Responder:
    return lambda request: httpx.Response(status, text="synthetic failure")
