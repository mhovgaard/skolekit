"""Phase 0 safety net: each collector against the synthetic SkoleIntra site.

These pin today's requests and records so the profile, transport and source
moves (architecture review phases 1-4) can be checked against them. The site and
its fixtures are in `skoleintra_site.py` and `fixtures/skoleintra_site/`.
"""
import json
import re
from urllib.parse import parse_qs

import httpx
import pytest

from tests_python.household_guard import household_patterns, offenders
from skolekit.sources import calendar as calendar_source
from skoleklar.harvest import HarvestPolicy, collect_all, scope_for
from skolekit.sources import (
    CollectionError,
    collect_calendar,
    collect_forms,
    collect_frontpage,
    collect_weekly_plans,
)
from skolekit.parsers.messages import parse_inbox_html
from skolekit.sources.messages import fetch_all_conversations, fetch_thread_messages
from tests_python.skoleintra_site import (
    ALFA,
    BETA,
    CHILDREN,
    FIXTURES,
    NOW,
    SCHOOL,
    SyntheticSite,
    frozen_datetime,
    html_page,
    json_response,
    status_response,
)


@pytest.fixture
def site() -> SyntheticSite:
    return SyntheticSite()


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(calendar_source, "datetime", frozen_datetime())


def pagings_sent(site: SyntheticSite) -> list[dict]:
    return [
        json.loads(parse_qs(request.content.decode())["pagings"][0])
        for request in site.requests
        if request.method == "POST"
    ]


def raw_pagings_sent(site: SyntheticSite) -> list[str]:
    return [
        parse_qs(request.content.decode())["pagings"][0]
        for request in site.requests
        if request.method == "POST"
    ]


# Fixture hygiene


def test_fixtures_contain_no_real_school_identifiers() -> None:
    """The household's own children, IDs and school host never appear in a fixture.

    They are read from the owner's files (tests_python/household_guard.py), so the
    repository never holds them, not even in this test."""
    if household_patterns() is None:
        pytest.skip("the owner's profile files are not on this machine")
    roots = [FIXTURES, FIXTURES.parent / "collect_all", FIXTURES.parent / "school"]
    files = [path for root in roots for path in root.rglob("*") if path.is_file()]
    assert len(files) > 30
    assert offenders(roots) == []


# Front page


def test_frontpage_first_page_is_the_server_rendered_index(site) -> None:
    with site.client() as client:
        check = collect_frontpage(client, ALFA, page_limit=1)

    assert site.routes() == [("GET", "Index")]
    assert [record.source_key for record in check.records] == [
        "skoleintra-pin:3101",
        "news:7003",
        "news:7002",
    ]
    # A show-more endpoint exists, so one page is a bounded window, not the feed.
    assert check.complete is True
    assert check.metadata == {"pages": 1, "exhausted": False}
    assert check.coverage == {"kind": "page_window", "pages": 1}

    notice = check.records[1]
    index_url = f"{SCHOOL.base_url}/parent/9101/Alfa/Index"
    assert notice.source_type == "frontpage"
    assert notice.source_url == f"{index_url}#news:7003"
    assert notice.title == "Tur til skoven onsdag"
    assert notice.body_text.splitlines() == [
        "Tur til skoven onsdag",
        "Vi går i skoven onsdag den 18. november.",
        "Medbring madpakke, drikkedunk og regntøj.",
        "Husk at",
        "tilmelde jer juleklip",
        "inden fredag.",
        "Vis mere",
        "tilmelding",
    ]
    assert notice.body_html.startswith('<div class="sk-news-item-content">')
    assert notice.dates == [
        {"kind": "display_timestamp", "value": "11. nov. 2026 14:05"},
        {"kind": "display_timestamp", "value": "Opdateret 11. nov. 2026 15:30"},
    ]
    assert notice.attachments == [
        {
            "url": f"{SCHOOL.base_url}/files/synthetic/tur-pakkeliste.pdf",
            "title": "Pakkeliste (PDF)",
        }
    ]
    # Links are absolute, de-duplicated, in page order, without `javascript:`.
    assert notice.links == [
        f"{SCHOOL.base_url}/parent/9101/Alfa/forms/open/survey/501",
        f"{SCHOOL.base_url}/files/synthetic/tur-pakkeliste.pdf",
    ]
    assert notice.source_data == {
        "feed_type": "news",
        "feed_id": "7003",
        "author": "Klasselærer (KL)",
        "audience": "1. klasse",
        "footer_text": "Vises til 19. nov. 2026",
        "timestamps": ["11. nov. 2026 14:05", "Opdateret 11. nov. 2026 15:30"],
    }
    assert notice.content_data == {
        "author": "Klasselærer (KL)",
        "audience": "1. klasse",
        "footer_text": "Vises til 19. nov. 2026",
    }
    assert notice.state_data == {}
    assert notice.raw_payload.startswith(
        '<div class="sk-news-item" data-feed-item-id="7003" data-feed-item-type="news">'
    )
    # A notice without a footer or author keeps empty strings.
    assert check.records[2].source_data["footer_text"] == ""
    assert check.records[0].dates == [
        {"kind": "display_timestamp", "value": "2. nov. 2026 08:00"}
    ]


def test_frontpage_follows_per_feed_cursors_until_the_feed_is_exhausted(site) -> None:
    with site.client() as client:
        check = collect_frontpage(client, ALFA, page_limit=None)

    # Each POST sends the last feed ID seen per feed type, as sorted JSON.
    assert raw_pagings_sent(site) == [
        '{"news": "7002", "skoleintra-pin": "3101"}',
        '{"news": "7001", "skoleintra-pin": "3100"}',
    ]
    assert site.routes() == [
        ("GET", "Index"),
        ("POST", "newsitems/showmore"),
        ("POST", "newsitems/showmore"),
    ]
    # The repeated news:7001 on the last page is dropped; the first copy stays.
    assert [record.source_key for record in check.records] == [
        "skoleintra-pin:3101",
        "news:7003",
        "news:7002",
        "news:7001",
        "skoleintra-pin:3100",
        "news:7000",
    ]
    assert check.records[3].attachments[0]["title"] == "Referat"
    assert check.records[3].source_url.endswith("/parent/9101/Alfa/Index#news:7001")
    assert check.complete is True
    assert check.metadata == {"pages": 3, "exhausted": True}
    assert check.coverage == {"kind": "full"}


def test_frontpage_page_limit_counts_the_index_as_page_one(site) -> None:
    with site.client() as client:
        check = collect_frontpage(client, ALFA, page_limit=2)

    assert len(pagings_sent(site)) == 1
    assert len(check.records) == 5
    assert check.complete is True
    assert check.metadata == {"pages": 2, "exhausted": False}
    assert check.coverage == {"kind": "page_window", "pages": 2}


def test_frontpage_stops_when_a_page_adds_nothing_new(site) -> None:
    index_items = site.text(ALFA, "index.html")
    site.override(
        ALFA,
        "newsitems/showmore",
        json_response({"View": index_items, "HasMoreItems": True}),
    )
    with site.client() as client:
        check = collect_frontpage(client, ALFA, page_limit=None)

    assert len(pagings_sent(site)) == 1
    assert len(check.records) == 3
    assert check.complete is False
    assert check.metadata == {"pages": 2, "exhausted": False}
    assert check.coverage == {"kind": "page_window", "pages": 2}


def test_frontpage_stops_on_a_repeated_cursor(site) -> None:
    # One new notice, then two old ones that put the cursor back where it was.
    view = (
        '<div class="sk-news-item" data-feed-item-type="news" data-feed-item-id="7500">'
        '<div class="sk-news-item-content"><p>Nyt opslag</p></div></div>'
        '<div class="sk-news-item" data-feed-item-type="news" data-feed-item-id="7002"></div>'
        '<div class="sk-news-item" data-feed-item-type="skoleintra-pin" data-feed-item-id="3101"></div>'
    )
    site.override(ALFA, "newsitems/showmore", json_response({"View": view, "HasMoreItems": True}))
    with site.client() as client:
        check = collect_frontpage(client, ALFA, page_limit=None)

    assert raw_pagings_sent(site) == ['{"news": "7002", "skoleintra-pin": "3101"}']
    assert [record.source_key for record in check.records][-1] == "news:7500"
    assert check.complete is False
    assert check.metadata == {"pages": 2, "exhausted": False}


def test_frontpage_without_show_more_is_the_whole_feed(site) -> None:
    with site.client() as client:
        check = collect_frontpage(client, BETA, page_limit=None)

    assert site.routes() == [("GET", "Index")]
    assert [record.source_key for record in check.records] == [
        "skoleintra-pin:3101",
        "news:8104",
    ]
    # A long first line is cut at 200 characters; this one is shorter.
    assert check.records[1].title == "Svømning starter i uge 48. Husk badetøj og håndklæde."
    assert check.metadata == {"pages": 1, "exhausted": True}
    assert check.coverage == {"kind": "full"}


def test_frontpage_show_more_failure_fails_the_whole_source(site) -> None:
    site.override(ALFA, "newsitems/showmore", status_response(500))
    with site.client() as client, pytest.raises(httpx.HTTPStatusError):
        collect_frontpage(client, ALFA, page_limit=None)


def test_frontpage_rejects_a_page_limit_below_one(site) -> None:
    with site.client() as client, pytest.raises(ValueError, match="at least 1"):
        collect_frontpage(client, ALFA, page_limit=0)
    assert site.requests == []


# Forms


def test_forms_reads_open_and_closed_lists_details_and_answer_pages(site) -> None:
    with site.client() as client:
        check = collect_forms(client, ALFA)

    # GET only: list, then each item's detail and answer page, open before closed.
    assert site.routes() == [
        ("GET", "forms/open"),
        ("GET", "forms/open/survey/501"),
        ("GET", "forms/open/survey/501/answer"),
        ("GET", "forms/open/conversations/812/edit"),
        ("GET", "forms/closed"),
        ("GET", "forms/closed/consent/455"),
    ]
    assert site.forbidden == []
    assert check.metadata == {"open": 2, "closed": 1}
    assert check.coverage == {"kind": "full"}
    assert check.complete is True
    # The key is `{type}:{id}` whether the link is open, closed or ends in /edit.
    assert [record.source_key for record in check.records] == [
        "survey:501",
        "conversations:812",
        "consent:455",
    ]

    survey, booking, consent = check.records
    base = f"{SCHOOL.base_url}/parent/9101/Alfa"
    assert survey.source_url == f"{base}/forms/open/survey/501"
    assert survey.title == "Tilmelding til juleklip"
    assert survey.body_text.splitlines() == [
        "Tilmelding til juleklip",
        "Vi holder juleklip fredag den 27. november kl. 9-12.",
        "Forældre er velkomne.",
        "Se",
        "programmet",
        "og",
        "sidste års blanket",
        ".",
        "Besvar",
    ]
    assert survey.dates == [{"kind": "deadline", "value": "20-11-2026"}]
    # Attachments leave out the answer link and links to other open forms.
    assert survey.attachments == [
        {"url": f"{SCHOOL.base_url}/files/synthetic/juleklip-program.pdf", "title": ""}
    ]
    assert survey.links == [
        f"{base}/forms/open/survey/501",
        f"{base}/forms/open/survey/501/answer",
    ]
    assert survey.state_data == {"list_state": "open", "status": "Ikke besvaret"}
    # Only select, textarea, radio and checkbox controls; a text input is not read.
    assert survey.source_data["questions"] == [
        {
            "name": "Answers[0].Value",
            "type": "select",
            "label": "Deltager I?",
            "options": [
                {"text": "Vælg", "value": "", "selected": False},
                {"text": "Ja", "value": "yes", "selected": True},
                {"text": "Nej", "value": "no", "selected": False},
            ],
            "value": "",
        },
        {
            "name": "Answers[2].Value",
            "type": "textarea",
            "label": "Bemærkninger",
            "options": [],
            "value": "Vi tager kage med.",
        },
        {"name": "Answers[3].Value", "type": "radio", "label": "Formiddag", "options": [], "value": "morning"},
        {"name": "Answers[3].Value", "type": "radio", "label": "Eftermiddag", "options": [], "value": "afternoon"},
        {"name": "Answers[4].Value", "type": "checkbox", "label": "Jeg vil gerne hjælpe til", "options": [], "value": "true"},
        {"name": "Answers[5].Value", "type": "checkbox", "label": "", "options": [], "value": "unlabelled"},
    ]
    assert survey.source_data["creator"] == "Klasselærer (KL)"
    assert survey.source_data["order_date"] == "2026-11-10T09:00:00"
    assert survey.content_data == {
        "creator": "Klasselærer (KL)",
        "type": "Tilmelding",
        "questions": survey.source_data["questions"],
    }
    assert set(survey.raw_payload) == {"list_item", "detail_html", "questions"}

    assert booking.source_url == f"{base}/forms/open/conversations/812/edit"
    assert booking.links == [f"{base}/forms/open/conversations/812/edit"]
    assert booking.source_data["questions"] == []
    assert booking.source_data["results_link"] == "/parent/9101/Alfa/forms/results/conversations/812"
    assert booking.state_data == {"list_state": "open", "status": "Min tid"}

    assert consent.dates == []
    assert consent.state_data == {"list_state": "closed", "status": "Besvaret"}
    assert consent.source_data["open_in_new_tab"] is True
    assert consent.source_data["is_app"] is True
    assert consent.attachments == [
        {"url": f"{SCHOOL.base_url}/files/synthetic/samtykke-vilkaar.pdf", "title": ""}
    ]


def test_forms_list_without_its_payload_raises(site) -> None:
    site.override(ALFA, "forms/closed", lambda request: httpx.Response(200, text="<html></html>"))
    with site.client() as client, pytest.raises(CollectionError, match="Forms payload missing for Alfa/closed"):
        collect_forms(client, ALFA)


def test_forms_detail_redirected_to_login_raises(site) -> None:
    site.redirect_to_login(ALFA, "forms/open/survey/501")
    with site.client() as client, pytest.raises(
        CollectionError, match="Authenticated GET failed for /parent/9101/Alfa/forms/open/survey/501"
    ):
        collect_forms(client, ALFA)


# Weekly plans


def test_weekly_plans_pick_the_latest_weeks_by_year_then_week(site) -> None:
    with site.client() as client:
        check = collect_weekly_plans(client, ALFA, scope=scope_for(HarvestPolicy.for_mode("refresh")))

    # The list has weeks 9, 47 and 46 (twice) plus a student plan and the archive.
    assert site.routes() == [
        ("GET", "item/weeklyplansandhomework/list"),
        ("GET", "item/weeklyplansandhomework/item/class/46-2026"),
        ("GET", "item/weeklyplansandhomework/item/class/47-2026"),
    ]
    assert check.metadata == {"weeks": 2, "available_weeks": 3}
    assert check.coverage == {
        "kind": "record_keys",
        "source_keys": ["class:46-2026", "class:47-2026"],
    }

    plan = check.records[0]
    assert plan.source_key == "class:46-2026"
    assert plan.source_url == (
        f"{SCHOOL.base_url}/parent/9101/Alfaitem/weeklyplansandhomework/item/class/46-2026"
    )
    assert plan.title == "Uge 46"
    assert plan.dates == [
        {"kind": "source_day", "value": f"2026-11-{day:02d}T00:00:00"} for day in range(9, 14)
    ]
    # General plans first, then each day's plans; empty texts are left out.
    assert plan.body_text.splitlines() == [
        "Kære forældre",
        "I denne uge arbejder vi med efteråret.",
        "Medbring: Gummistøvler hver dag.",
        "Lektie: Læs side 12 i læsebogen.",
        "Materialer: Læsebog og blyant.",
        "Aktivitet:",
        "Vi samler blade i skolegården.",
        "Medbring: En pose.",
        "Vi øver plus til 20.",
        "Dagens program: Boldspil i hallen.",
        "Medbring idrætstøj.",
    ]
    assert plan.attachments == [
        {"Name": "ugeplan-46.pdf", "Url": "/files/synthetic/ugeplan-46.pdf"},
        {"Name": "efteraar.pdf", "Url": "/files/synthetic/efteraar.pdf"},
        {"Name": "arbejdsark.pdf", "Url": "/files/synthetic/arbejdsark.pdf"},
    ]
    assert plan.state_data == {"has_drafts": True}
    assert plan.source_data is plan.raw_payload
    assert plan.source_data["week"] == {
        "number": 46,
        "year": 2026,
        "label": "Uge 46",
        "class_or_group": "1. klasse",
    }
    assert set(plan.content_data) == {"week", "general_plans", "days"}
    monday = plan.source_data["days"][0]["plans"][0]
    assert [segment["section_hint"] for segment in monday["segments"]] == ["homework", "materials"]
    assert check.records[1].state_data == {"has_drafts": False}


def test_weekly_plans_initialize_reads_every_week_in_order(site) -> None:
    with site.client() as client:
        check = collect_weekly_plans(client, ALFA, scope=scope_for(HarvestPolicy.for_mode("initialize")))

    assert [record.source_key for record in check.records] == [
        "class:9-2026",
        "class:46-2026",
        "class:47-2026",
    ]
    assert check.records[0].dates == []
    assert check.metadata == {"weeks": 3, "available_weeks": 3}
    assert check.coverage == {"kind": "full"}


def test_weekly_plans_page_without_its_payload_raises(site) -> None:
    site.override(
        ALFA,
        "item/weeklyplansandhomework/item/class/47-2026",
        lambda request: httpx.Response(200, text='<div id="root"></div>'),
    )
    with site.client() as client, pytest.raises(ValueError, match="settings element was not found"):
        collect_weekly_plans(client, ALFA)


# Calendar


def test_calendar_requests_each_feed_in_28_day_chunks(site) -> None:
    policy = HarvestPolicy.for_mode("refresh")
    with site.client() as client:
        check = collect_calendar(client, ALFA, scope=scope_for(policy))

    # Refresh: from Monday a week back to seven Mondays ahead, 56 days, two chunks.
    windows = [
        (int(NOW.replace(day=2, hour=0).timestamp()), int(NOW.replace(day=30, hour=0).timestamp())),
        (int(NOW.replace(day=30, hour=0).timestamp()), int(NOW.replace(month=12, day=28, hour=0).timestamp())),
    ]
    requests = [
        (request.url.path.rsplit("/", 1)[1], dict(request.url.params))
        for request in site.requests
    ]
    expected = []
    for kind in ("ActivitiesEvents", "SchoolEvents", "LessonsEvents"):
        for start, end in windows:
            params = {"departmentIds": '["0"]', "start": str(start), "end": str(end)}
            if kind == "LessonsEvents":
                params["className"] = "001"
            expected.append((kind, params))
    assert requests == expected
    assert check.metadata == {
        "counts": {"ActivitiesEvents": 3, "SchoolEvents": 3, "LessonsEvents": 4},
        "range_start": "2026-11-02T00:00:00+01:00",
        "range_end": "2026-12-28T00:00:00+01:00",
        "requests": 6,
    }
    assert check.coverage == {"kind": "date_window", "start": "2026-11-02", "end": "2026-12-28"}

    keys = [record.source_key for record in check.records]
    # The theme week spans both chunks and is kept once. The same event in two
    # feeds keeps one record per feed.
    assert keys[:6] == [
        "ActivitiesEvents:880101",
        "ActivitiesEvents:880150",
        "ActivitiesEvents:880103",
        "SchoolEvents:880201",
        "SchoolEvents:880150",
        "SchoolEvents:880202",
    ]
    lessons = check.records[6:]
    # Two copies of the same lesson slot with different volatile IDs are one
    # record; a lesson without staff falls back to its originalId.
    assert [record.title for record in lessons] == ["Dansk", "Matematik", "Musik", "Dansk"]
    assert lessons[0].source_key.startswith("LessonsEvents:slot:")
    assert lessons[0].raw_payload["id"] == "2_9f1e0001"
    assert lessons[2].source_key == "LessonsEvents:990004"
    assert lessons[0].source_key != lessons[3].source_key

    meeting = check.records[0]
    assert meeting.source_url == f"{SCHOOL.base_url}/parent/9101/Alfa/calendareventsource/ActivitiesEvents"
    assert meeting.body_text == "Vi mødes i klasselokalet.\nDer er kaffe og te."
    assert meeting.dates == [
        {"kind": "event", "start": "2026-11-17T19:00:00", "end": "2026-11-17T20:30:00", "all_day": False}
    ]
    assert meeting.links == ["/parent/9101/Alfa/messages/conversations#thread-301"]
    assert meeting.attachments == [{"Name": "dagsorden.pdf", "Url": "/files/synthetic/dagsorden.pdf"}]
    assert meeting.source_data == {"calendar_kind": "ActivitiesEvents", "event": meeting.raw_payload}
    assert meeting.state_data == {"calendar_kind": "ActivitiesEvents"}
    assert list(meeting.content_data) == [
        "title",
        "description",
        "location",
        "authorName",
        "invitees",
        "Attachments",
        "IsPrivate",
    ]
    holiday = check.records[5]
    assert holiday.dates == [
        {"kind": "event", "start": "2026-12-21T00:00:00", "end": "2027-01-04T00:00:00", "all_day": True}
    ]
    substitute = lessons[3]
    assert substitute.links == ["/parent/9101/Alfa/substitutions"]
    assert substitute.content_data["substituteStaff"] == "VI"
    assert lessons[2].title == "Musik"


def test_calendar_identity_collision_between_chunks_raises(site) -> None:
    events = site.json(ALFA, "calendar-ActivitiesEvents.json")
    changed = [dict(event, title="Temauge: Havet") for event in events]
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=events if len(calls) == 1 else changed)

    site.override(ALFA, "calendareventsource/ActivitiesEvents", respond)
    with site.client() as client, pytest.raises(
        CollectionError, match="Calendar identity collision for ActivitiesEvents:880104"
    ):
        collect_calendar(client, ALFA, scope=scope_for(HarvestPolicy.for_mode("refresh")))


def test_calendar_identical_repeats_between_chunks_are_kept_once(site) -> None:
    events = site.json(ALFA, "calendar-ActivitiesEvents.json")
    site.override(ALFA, "calendareventsource/ActivitiesEvents", json_response(events))
    with site.client() as client:
        check = collect_calendar(client, ALFA, scope=scope_for(HarvestPolicy.for_mode("refresh")))

    assert check.metadata["counts"]["ActivitiesEvents"] == 4
    assert check.metadata["requests"] == 6


def test_calendar_response_that_is_not_a_list_raises(site) -> None:
    site.override(ALFA, "calendareventsource/SchoolEvents", json_response({"events": []}))
    with site.client() as client, pytest.raises(CollectionError, match="Calendar SchoolEvents response was not a list"):
        collect_calendar(client, ALFA)


def test_calendar_initialize_reads_the_school_year_in_utc_chunks(site) -> None:
    with site.client() as client:
        check = collect_calendar(client, BETA, scope=scope_for(HarvestPolicy.for_mode("initialize")))

    # The school year (1/8 to 1/8) in 28-day chunks: 13 full chunks and one of 1 day.
    assert check.metadata["requests"] == 42
    assert check.metadata["range_start"] == "2026-08-01T00:00:00+00:00"
    assert check.metadata["range_end"] == "2027-08-01T00:00:00+00:00"
    assert check.coverage == {"kind": "date_window", "start": "2026-08-01", "end": "2027-08-01"}
    last = site.requests[13].url.params
    assert (int(last["end"]) - int(last["start"])) == 86400
    assert [record.source_key.split(":")[0] for record in check.records] == [
        "SchoolEvents",
        "LessonsEvents",
        "LessonsEvents",
    ]


# Reading the inbox live
#
# Message text is never stored (the legacy mode that kept messages as records
# was removed on 14 November 2026). The fetchers read a thread live when
# someone asks, with GETs only, and never leave the inbox's own host.


def read_inbox(client, child, *, page_limit=None):
    response = client.get(child.route("messages/conversations"))
    inbox = parse_inbox_html(response.text, str(response.url))
    conversations, complete, exhausted, pages = fetch_all_conversations(
        client, inbox, max_pages=100, page_limit=page_limit)
    return inbox, conversations, (complete, exhausted, pages)


def test_reading_threads_live_uses_read_only_gets(site) -> None:
    with site.client() as client:
        inbox, conversations, scope = read_inbox(client, ALFA)
        threads = [fetch_thread_messages(client, inbox, conversation) for conversation in conversations]

    assert scope == (True, True, 2)
    assert site.forbidden == []
    assert {request.method for request in site.requests} == {"GET"}
    assert site.routes() == [
        ("GET", "messages/conversations"),
        ("GET", "messages/conversations/getnewconversations"),
        ("GET", "messages/conversations/getnewconversations"),
        ("GET", "messages/conversations/expandthreadmessages"),
        ("GET", "messages/conversations/loadmessagesforselectedconversation"),
        ("GET", "messages/conversations/expandthreadmessages"),
        ("GET", "messages/conversations/loadmessagesforselectedconversation"),
        ("GET", "messages/conversations/getmessageforthreadlessconversation"),
        ("GET", "messages/conversations/expandthreadmessages"),
        ("GET", "messages/conversations/loadmessagesforselectedconversation"),
    ]
    page_cursors = [
        dict(request.url.params) for request in site.requests if "getnew" in request.url.path
    ]
    assert page_cursors == [
        {"takeFromRootMessageId": "950", "searchRequest": "", "filter": ""},
        {"takeFromRootMessageId": "3032", "searchRequest": "", "filter": ""},
    ]
    assert dict(site.requests[3].url.params) == {"threadId": "301", "latestMessageId": "3012"}
    assert dict(site.requests[4].url.params) == {
        "threadId": "301",
        "takeFromRootMessageId": "3011",
        "takeToMessageId": "3012",
        "searchRequest": "",
    }
    assert dict(site.requests[7].url.params) == {"messageId": "950"}
    # The threadless message's conversation has thread 0.
    assert [conversation["thread_id"] for conversation in conversations] == [301, 302, 0, 303]
    assert threads[0][0]["body_text"] == "Kære forældre\nOnsdag går vi i skoven. Husk regntøj."


def test_reading_the_inbox_live_with_one_page_reads_only_the_inbox_page(site) -> None:
    with site.client() as client:
        _, conversations, scope = read_inbox(client, ALFA, page_limit=1)

    assert ("GET", "messages/conversations/getnewconversations") not in site.routes()
    assert len(conversations) == 3 and scope[2] == 1


def test_an_inbox_endpoint_on_another_host_is_refused(site) -> None:
    inbox = site.json(ALFA, "messages-inbox.json")
    inbox["DataProviderSettings"]["ExpandThreadMessagesUrl"] = "https://example.invalid/expand"
    page = site.text(ALFA, "messages-inbox.html")
    site.override(
        ALFA,
        "messages/conversations",
        lambda request: html_page(page, payload=inbox),
    )
    with site.client() as client, pytest.raises(ValueError, match="outside the inbox origin"):
        read_inbox(client, ALFA)
    assert len(site.requests) == 1


# collect_all


def test_collect_all_runs_each_collector_per_child_in_a_fixed_order(site) -> None:
    with site.client() as client:
        checks = collect_all(client, children=CHILDREN, policy=HarvestPolicy.for_mode("daily"))

    assert [(check.child, check.source_type) for check in checks] == [
        (child.child, source)
        for child in CHILDREN
        for source in ("frontpage", "messages", "calendar", "weekly_plan", "forms")
    ]
    assert all(check.status == "ok" for check in checks)
    # Messages are off by default: no inbox request, an excluded placeholder.
    assert not any("messages" in route for _, route in site.routes())
    messages = checks[1]
    assert messages.records == []
    assert messages.complete is True
    assert messages.metadata == {"message_mode": "off", "excluded_by_policy": True, "bodies_retained": 0}
    assert messages.coverage == {"kind": "excluded_by_policy"}


def test_collect_all_events_only_leaves_a_placeholder_for_the_restricted_inbox(site) -> None:
    policy = HarvestPolicy.for_mode("daily", message_mode="events-only", message_processor="zdr")
    with site.client() as client:
        checks = collect_all(client, children=(BETA,), policy=policy)

    assert not any("messages" in route for _, route in site.routes())
    assert checks[1].metadata == {
        "message_mode": "events-only",
        "excluded_by_policy": False,
        "bodies_retained": 0,
    }
    assert checks[1].coverage == {"kind": "excluded_by_policy"}


def test_collect_all_isolates_each_source_failure(site) -> None:
    site.override(ALFA, "calendareventsource/SchoolEvents", status_response(500))
    site.override(ALFA, "forms/open", lambda request: httpx.Response(200, text="<html></html>"))
    site.redirect_to_login(BETA, "Index")

    def disconnected(request):
        raise httpx.ConnectError("synthetic connection reset", request=request)

    site.override(BETA, "item/weeklyplansandhomework/list", disconnected)
    site.override(BETA, "messages/conversations/getnewconversations", status_response(503))
    with site.client() as client:
        checks = collect_all(client, children=CHILDREN, policy=HarvestPolicy.for_mode("initialize"))

    summary = [(check.child, check.source_type, check.status, check.error) for check in checks]
    assert summary == [
        ("Alfa", "frontpage", "ok", None),
        ("Alfa", "messages", "ok", None),
        ("Alfa", "calendar", "error",
         "CollectionError: Authenticated GET failed for /parent/9101/Alfa/calendareventsource/SchoolEvents"),
        ("Alfa", "weekly_plan", "ok", None),
        ("Alfa", "forms", "error", "CollectionError: Forms payload missing for Alfa/open"),
        ("Beta", "frontpage", "error", "CollectionError: Authenticated GET failed for /parent/9202/Beta/Index"),
        ("Beta", "messages", "ok", None),
        ("Beta", "calendar", "ok", None),
        ("Beta", "weekly_plan", "error", "ConnectError: synthetic connection reset"),
        ("Beta", "forms", "ok", None),
    ]
    for check in checks:
        if check.error:
            # A failed source keeps nothing: no partial records, no coverage claim.
            assert check.records == []
            assert check.complete is False
            assert check.metadata == {}
            assert check.coverage == {"kind": "full"}
    assert len(checks[0].records) == 6
    # Messages are never records: a complete, empty check.
    assert checks[1].records == [] and checks[1].complete is True
    # The calendar stops at the failing feed; later feeds are not requested.
    alfa_calendar = [route for _, route in site.routes(ALFA) if route.startswith("calendar")]
    assert alfa_calendar[-1] == "calendareventsource/SchoolEvents"
    assert "calendareventsource/LessonsEvents" not in alfa_calendar
    # The harvest never reads the inbox, so the 503 override is never hit.
    assert not any("messages" in route for _, route in site.routes())
