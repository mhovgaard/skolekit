from types import SimpleNamespace

from skoleklar.cli import build_parser
from skoleklar.cli.common import html_title, json_shape
from skoleklar.progress import _emit_progress
from skolekit.transport import is_allowed_url, is_authenticated_page, sanitize_url


def test_only_allows_https_skoleintra_hosts() -> None:
    assert is_allowed_url("https://eksempel.m.skoleintra.dk/parent/9202")
    assert not is_allowed_url("http://eksempel.m.skoleintra.dk/parent/9202")
    assert not is_allowed_url("https://skoleintra.dk.example.com/parent/9202")


def test_sanitizes_sensitive_query_parameters() -> None:
    result = sanitize_url(
        "https://x.skoleintra.dk/page?child=9202&access_token=secret&state=abc"
    )
    assert "child=9202" in result
    assert "secret" not in result
    assert "abc" not in result
    assert result.count("%5BREDACTED%5D") == 2


def test_json_shape_keeps_structure_but_not_values() -> None:
    shape = json_shape({"student": {"name": "Beta", "id": 9202}, "items": [1, 2]})
    assert shape["student"] == {"name": "string", "id": "number"}
    assert shape["items"]["type"] == "list"
    assert "Beta" not in str(shape)


def test_login_redirect_is_not_authenticated() -> None:
    assert not is_authenticated_page(
        "https://x.skoleintra.dk/Account/IdpLogin?returnUrl=%2Fparent"
    )
    assert is_authenticated_page("https://x.skoleintra.dk/parent/9202/Beta")


def test_extracts_html_title_without_body_content() -> None:
    assert html_title("<html><title> Kalender  -  ForældreIntra </title></html>") == (
        "Kalender - ForældreIntra"
    )


def test_parses_explicit_headless_auto_login_options() -> None:
    args = build_parser().parse_args(
        ["login", "--use-keychain", "--auto-submit", "--headless"]
    )

    assert args.use_keychain is True
    assert args.auto_submit is True
    assert args.headless is True


def test_parses_harvest_auto_login() -> None:
    args = build_parser().parse_args(["harvest", "--auto-login"])

    assert args.auto_login is True
    assert args.auto_login_timeout == 2
    assert args.mode == "refresh"
    assert args.message_pages is None
    assert args.message_concurrency == 2
    assert args.progress == "auto"


def test_parses_selective_harvest_scope() -> None:
    args = build_parser().parse_args(
        [
            "harvest",
            "--mode",
            "latest",
            "--message-pages",
            "3",
            "--calendar-future-weeks",
            "4",
        ]
    )

    assert args.mode == "latest"
    assert args.message_pages == 3
    assert args.calendar_future_weeks == 4


def test_parses_daily_harvest_alias() -> None:
    args = build_parser().parse_args(["harvest", "--mode", "daily"])

    assert args.mode == "daily"


def test_parses_harvest_progress_and_concurrency() -> None:
    args = build_parser().parse_args(
        ["harvest", "--message-concurrency", "3", "--progress", "plain"]
    )

    assert args.message_concurrency == 3
    assert args.progress == "plain"


def test_plain_progress_uses_stderr_and_keeps_stdout_clean(capsys) -> None:
    _emit_progress(
        SimpleNamespace(progress="plain"),
        "ZDR events",
        detail="2/7 · 14s",
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ZDR events · 2/7 · 14s" in captured.err


def test_parses_llm_tool_commands() -> None:
    derive = build_parser().parse_args(
        ["derive", "--run-id", "8", "--source", "weekly_plan", "--dry-run"]
    )
    brief = build_parser().parse_args(
        ["brief", "--date", "2026-08-24", "--format", "json", "--record-delivery"]
    )
    week_brief = build_parser().parse_args(
        [
            "brief",
            "--from",
            "2026-09-14",
            "--to",
            "2026-09-20",
            "--compact",
        ]
    )
    item = build_parser().parse_args(["item", "acknowledge", "12"])
    backfill = build_parser().parse_args(
        [
            "derive",
            "--backfill-since",
            "2026-08-01",
            "--source",
            "frontpage",
            "--limit",
            "25",
        ]
    )
    report = build_parser().parse_args(
        ["derivation-report", "--source", "frontpage", "--since", "2026-08-01"]
    )
    consolidate = build_parser().parse_args(
        ["consolidate", "--as-of", "2026-08-24", "--limit", "50", "--dry-run"]
    )
    archive = build_parser().parse_args(
        ["archive", "--as-of", "2026-09-12", "--dry-run"]
    )
    consolidation_report = build_parser().parse_args(
        ["consolidation-report", "--consolidation-id", "3"]
    )
    manifest = build_parser().parse_args(
        ["manifest", "--date", "2026-08-24", "--delivery-root", "/tmp/delivery"]
    )
    sync = build_parser().parse_args(
        ["sync", "--date", "2026-08-24", "--mode", "daily"]
    )

    assert derive.source == ["weekly_plan"]
    assert derive.dry_run is True
    assert brief.record_delivery is True
    assert week_brief.from_date == "2026-09-14"
    assert week_brief.to_date == "2026-09-20"
    assert week_brief.compact is True
    assert item.item_action == "acknowledge"
    assert backfill.backfill_since == "2026-08-01"
    assert backfill.limit == 25
    assert report.source == ["frontpage"]
    assert consolidate.as_of == "2026-08-24"
    assert consolidate.limit == 50
    assert consolidate.dry_run is True
    assert archive.as_of == "2026-09-12"
    assert archive.dry_run is True
    assert archive.no_agent_review is False
    assert consolidation_report.consolidation_id == 3
    assert manifest.date == "2026-08-24"
    assert manifest.delivery_root == "/tmp/delivery"
    assert sync.mode == "daily"
