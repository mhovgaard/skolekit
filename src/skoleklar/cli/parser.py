"""The command grammar; command implementations load only when selected."""
from __future__ import annotations

import argparse
from datetime import date

from skoleklar.cli.dispatch import handler
from skoleklar.household import load_site_profile
from skoleklar.records_copy import saved_query_names
from skoleklar.settings import DEFAULT_MODEL
from skoleklar.triage.prompt_manifest import DEFAULT_MANIFEST_ID as PROMPT_VERSION
from skolekit.cli import add_live_commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skoleklar",
        description="Ready for school: the household's scan, board, phone and brief, built on skolekit",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="Open a private browser for manual login")
    login.add_argument("--url", help="Default: the profile's login page")
    login.add_argument("--timeout", type=int, default=15, help="Login timeout in minutes")
    login.add_argument(
        "--use-keychain",
        action="store_true",
        help="Fill saved Keychain credentials; submission remains manual by default",
    )
    login.add_argument(
        "--auto-submit",
        action="store_true",
        help="Submit Keychain credentials to the verified SkoleIntra login form",
    )
    login.add_argument(
        "--headless",
        action="store_true",
        help="Run automatic login without a visible browser window",
    )
    login.set_defaults(handler=handler("auth", "command_login"))

    credentials = subparsers.add_parser(
        "credentials", help="Manage optional macOS Keychain credentials"
    )
    credentials_subparsers = credentials.add_subparsers(
        dest="credentials_command", required=True
    )
    credentials_set = credentials_subparsers.add_parser("set")
    credentials_set.set_defaults(handler=handler("auth", "command_credentials_set"))
    credentials_status = credentials_subparsers.add_parser("status")
    credentials_status.set_defaults(handler=handler("auth", "command_credentials_status"))
    credentials_delete = credentials_subparsers.add_parser("delete")
    credentials_delete.set_defaults(handler=handler("auth", "command_credentials_delete"))

    llm_credentials = subparsers.add_parser(
        "llm-credentials", help="Manage the optional OpenRouter API key"
    )
    llm_credentials_subparsers = llm_credentials.add_subparsers(
        dest="llm_credentials_command", required=True
    )
    llm_credentials_set = llm_credentials_subparsers.add_parser("set")
    llm_credentials_set.set_defaults(handler=handler("auth", "command_llm_credentials_set"))
    llm_credentials_status = llm_credentials_subparsers.add_parser("status")
    llm_credentials_status.set_defaults(handler=handler("auth", "command_llm_credentials_status"))
    llm_credentials_delete = llm_credentials_subparsers.add_parser("delete")
    llm_credentials_delete.set_defaults(handler=handler("auth", "command_llm_credentials_delete"))

    check = subparsers.add_parser("check", help="Check whether the saved session works")
    check.add_argument("--url", help="Default: the profile's login page")
    check.set_defaults(handler=handler("auth", "command_check"))

    probe = subparsers.add_parser(
        "probe", help="Test a read-only endpoint without launching a browser"
    )
    probe.add_argument("--url", help="Default: the profile's login page")
    probe.set_defaults(handler=handler("auth", "command_probe"))

    weekly_plan = subparsers.add_parser(
        "weekly-plan", help="Normalize an embedded weekly-plan payload"
    )
    weekly_plan.add_argument("--url", required=True)
    weekly_plan.add_argument(
        "--include-content",
        action="store_true",
        help="Include private raw and plain-text plan content in stdout",
    )
    weekly_plan.set_defaults(handler=handler("auth", "command_weekly_plan"))

    message = subparsers.add_parser(
        "message", help="List or retrieve messages through the privacy boundary"
    )
    message_subparsers = message.add_subparsers(
        dest="message_command", required=True
    )
    message_list = message_subparsers.add_parser(
        "list", help="Show the minimized current inbox index"
    )
    message_list.add_argument("--db", help="Override the private SQLite path")
    message_list.add_argument(
        "--refresh", action="store_true", help="Refresh safe metadata from SkoleIntra"
    )
    message_list.add_argument(
        "--pages", type=int, default=1, help="Recent inbox pages to inspect"
    )
    message_list.add_argument(
        "--message-mode", choices=["off", "events-only"]
    )
    message_list.set_defaults(handler=handler("messages", "command_message_list"))

    message_get = message_subparsers.add_parser(
        "get", help="Retrieve one live message without storing its body"
    )
    message_get.add_argument(
        "thread_ref",
        nargs="?",
        help="Opaque thread handle from message list (omit when using --child)",
    )
    message_get.add_argument(
        "--child",
        help="Live-select the newest thread for one child, by the name in the profile",
    )
    selection = message_get.add_mutually_exclusive_group()
    selection.add_argument("--message", dest="message_ref")
    selection.add_argument(
        "--latest", action="store_true", help="Return the newest message (default)"
    )
    selection.add_argument(
        "--thread", action="store_true", help="Return every current message in the thread"
    )
    message_get.add_argument("--db", help="Override the private SQLite path")
    message_get.add_argument(
        "--pages", type=int, default=5, help="Inbox pages to search for the handle"
    )
    message_get.add_argument("--format", choices=["text", "json"], default="text")
    message_get.add_argument(
        "--message-mode", choices=["off", "events-only"]
    )
    message_get.set_defaults(handler=handler("messages", "command_message_get"))

    message_review = message_subparsers.add_parser(
        "review", help="Record content-free events-only evaluation feedback"
    )
    message_review.add_argument("thread_ref")
    message_review.add_argument(
        "verdict",
        choices=[
            "correct",
            "wrong_date",
            "wrong_time",
            "wrong_location",
            "not_event",
            "missed_event",
            "duplicate",
            "too_much_detail",
            "needed_source_open",
        ],
    )
    message_review.add_argument("--event", dest="event_key")
    message_review.add_argument("--db")
    message_review.add_argument(
        "--message-mode", choices=["off", "events-only"]
    )
    message_review.set_defaults(handler=handler("messages", "command_message_review"))

    message_evaluation = message_subparsers.add_parser(
        "evaluation", help="Report categorical events-only evaluation metrics"
    )
    message_evaluation.add_argument("--db")
    message_evaluation.add_argument(
        "--message-mode", choices=["off", "events-only"]
    )
    message_evaluation.set_defaults(handler=handler("messages", "command_message_evaluation"))

    harvest = subparsers.add_parser(
        "harvest", help="Collect both children and persist a private snapshot"
    )
    harvest.add_argument("--db", help="Override the private SQLite database path")
    harvest.add_argument(
        "--mode",
        choices=["initialize", "refresh", "daily", "latest"],
        default="refresh",
        help="Full baseline, bounded refresh, optimized daily, or latest-only",
    )
    harvest.add_argument(
        "--message-pages",
        "--max-message-pages",
        dest="message_pages",
        type=int,
        help="Total recent conversation pages per child (mode default: 1)",
    )
    harvest.add_argument(
        "--message-mode",
        choices=["off", "events-only"],
        help="Inbox privacy mode (default: AG_SCHOOL_MESSAGE_MODE or off)",
    )
    harvest.add_argument(
        "--message-processor",
        choices=["disabled", "local", "zdr"],
        help="Events-only processor (default: disabled)",
    )
    harvest.add_argument(
        "--model", default=DEFAULT_MODEL, help="Model used for ZDR message events"
    )
    harvest.add_argument(
        "--message-concurrency",
        type=int,
        choices=range(1, 5),
        default=2,
        help="Concurrent ZDR message requests (default: 2; maximum: 4)",
    )
    harvest.add_argument(
        "--progress",
        choices=["auto", "plain", "off"],
        default="auto",
        help="Progress on stderr (auto for interactive terminals)",
    )
    harvest.add_argument(
        "--frontpage-pages",
        type=int,
        help="Total recent front-page pages per child (mode default: 1)",
    )
    harvest.add_argument(
        "--weekly-plan-weeks",
        type=int,
        help="Most recent weekly plans per child (mode default: 2)",
    )
    harvest.add_argument(
        "--calendar-past-weeks",
        type=int,
        help="Complete calendar weeks before this week (mode default: 1)",
    )
    harvest.add_argument(
        "--calendar-future-weeks",
        type=int,
        help="Weeks after this week (refresh: 6; daily: 2; latest: 0)",
    )
    harvest.add_argument(
        "--auto-login",
        action="store_true",
        help="Renew an expired session headlessly using macOS Keychain",
    )
    harvest.add_argument(
        "--auto-login-timeout",
        type=int,
        default=2,
        help="Automatic login timeout in minutes",
    )
    harvest.set_defaults(handler=handler("lifecycle", "command_harvest", writer=True))

    diff = subparsers.add_parser(
        "diff", help="Show meaningful changes from a completed harvest"
    )
    diff.add_argument("--db", help="Override the private SQLite database path")
    diff.add_argument("--run-id", type=int)
    diff.add_argument(
        "--include-noise",
        action="store_true",
        help="Include unchanged and raw-only changes",
    )
    diff.set_defaults(handler=handler("records", "command_diff"))

    structure = subparsers.add_parser(
        "structure", help="Report snapshot sizes and counts without private content"
    )
    structure.add_argument("--db", help="Override the private SQLite database path")
    structure.add_argument("--run-id", type=int)
    structure.set_defaults(handler=handler("records", "command_structure"))

    derive = subparsers.add_parser(
        "derive", help="Use an LLM to derive atomic items from meaningful changes"
    )
    derive.add_argument("--db", help="Override the private SQLite database path")
    derive_selection = derive.add_mutually_exclusive_group()
    derive_selection.add_argument("--run-id", type=int)
    derive_selection.add_argument(
        "--backfill-since",
        help="Select current source records dated on/after YYYY-MM-DD",
    )
    derive.add_argument(
        "--source",
        action="append",
        choices=["frontpage", "calendar", "weekly_plan", "forms"],
        help="Limit derivation to a source type; repeat to include more than one",
    )
    derive.add_argument("--limit", type=int, help="Maximum candidate groups to process")
    derive.add_argument("--model", default=DEFAULT_MODEL)
    derive.add_argument(
        "--message-mode", choices=["off", "events-only"]
    )
    derive.add_argument(
        "--dry-run",
        action="store_true",
        help="Show source/version candidates without sending private content",
    )
    derive.set_defaults(handler=handler("lifecycle", "command_derive", writer=True))

    derivation_report = subparsers.add_parser(
        "derivation-report",
        help="Review prompt-version summaries without printing complete source bodies",
    )
    derivation_report.add_argument(
        "--db", help="Override the private SQLite database path"
    )
    derivation_report.add_argument(
        "--source",
        action="append",
        choices=["frontpage", "calendar", "weekly_plan", "forms"],
    )
    derivation_report.add_argument("--since")
    derivation_report.add_argument("--model", default=DEFAULT_MODEL)
    derivation_report.add_argument("--prompt-version", default=PROMPT_VERSION)
    derivation_report.add_argument(
        "--include-source-preview",
        action="store_true",
        help="Include the first 320 normalized source characters for local review",
    )
    derivation_report.set_defaults(handler=handler("records", "command_derivation_report"))

    consolidate = subparsers.add_parser(
        "consolidate",
        help="Consolidate semantically identical live items without publishing",
    )
    consolidate.add_argument(
        "--db", help="Override the private SQLite database path"
    )
    consolidate.add_argument("--as-of", default=date.today().isoformat())
    consolidate.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum complete input set; refuses partial consolidation",
    )
    consolidate.add_argument("--model", default=DEFAULT_MODEL)
    consolidate.add_argument(
        "--message-mode", choices=["off", "events-only"]
    )
    consolidate.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the complete LLM input without an API call or database write",
    )
    consolidate.set_defaults(handler=handler("lifecycle", "command_consolidate", writer=True))

    archive = subparsers.add_parser(
        "archive",
        help="Audit and archive obsolete items before consolidation",
    )
    archive.add_argument("--db", help="Override the private SQLite database path")
    archive.add_argument("--as-of", default=date.today().isoformat())
    archive.add_argument("--model", default=DEFAULT_MODEL)
    archive.add_argument(
        "--message-mode", choices=["off", "events-only"]
    )
    archive.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan decisions without an API call or database write",
    )
    archive.add_argument(
        "--no-agent-review",
        action="store_true",
        help="Retain ambiguous candidates instead of asking the archival agent",
    )
    archive.set_defaults(handler=handler("lifecycle", "command_archive", writer=True))

    archival_report = subparsers.add_parser(
        "archival-report",
        help="Show the latest or a selected persisted archival audit",
    )
    archival_report.add_argument(
        "--db", help="Override the private SQLite database path"
    )
    archival_report.add_argument("--archival-run-id", type=int)
    archival_report.set_defaults(handler=handler("records", "command_archival_report"))

    consolidation_report = subparsers.add_parser(
        "consolidation-report",
        help="Show the latest or a selected persisted consolidation",
    )
    consolidation_report.add_argument(
        "--db", help="Override the private SQLite database path"
    )
    consolidation_report.add_argument("--consolidation-id", type=int)
    consolidation_report.set_defaults(handler=handler("records", "command_consolidation_report"))

    day_summaries = subparsers.add_parser(
        "day-summaries",
        help="Retell each weekly plan as one child-friendly paragraph per school day",
    )
    day_summaries.add_argument("--db", help="Override the private SQLite database path")
    day_summaries.add_argument("--model", default=DEFAULT_MODEL)
    day_summaries.add_argument("--limit", type=int, default=6,
                               help="Most plans to summarize in this run")
    day_summaries.add_argument("--today", help="ISO date that selects this and next week's plans")
    day_summaries.add_argument(
        "--dry-run", action="store_true",
        help="List the plans that need a summary without sending plan content",
    )
    day_summaries.add_argument(
        "--show", metavar="DATE",
        help="Print the stored summaries for the school week containing DATE",
    )
    day_summaries.add_argument("--message-mode", choices=["off", "events-only"])
    day_summaries.set_defaults(handler=handler("records", "command_day_summaries", writer=True))

    brief = subparsers.add_parser(
        "brief", help="Render the traffic-light family brief from derived items"
    )
    brief.add_argument("--db", help="Override the private SQLite database path")
    brief_scope = brief.add_mutually_exclusive_group()
    brief_scope.add_argument("--date", help="Render one ISO date (default: today)")
    brief_scope.add_argument(
        "--from",
        dest="from_date",
        help="Start an inclusive ISO date range",
    )
    brief.add_argument(
        "--to",
        dest="to_date",
        help="End an inclusive range (defaults to --from)",
    )
    brief.add_argument(
        "--compact",
        action="store_true",
        help=(
            "Hide routine lessons and adaptively group repeated timetable "
            "changes in a date-range brief"
        ),
    )
    brief.add_argument("--format", choices=["text", "json"], default="text")
    brief.add_argument(
        "--message-mode", choices=["off", "events-only"]
    )
    brief.add_argument(
        "--message-processor", choices=["disabled", "local", "zdr"]
    )
    brief.add_argument(
        "--record-delivery",
        action="store_true",
        help="Record included items as surfaced after producing this brief",
    )
    brief.set_defaults(handler=handler("records", "command_brief", writer=True))

    manifest = subparsers.add_parser(
        "manifest", help="Build and publish a versioned iPhone delivery manifest"
    )
    manifest.add_argument("--db", help="Override the private SQLite database path")
    manifest.add_argument("--date", default=date.today().isoformat())
    manifest.add_argument(
        "--delivery-root", help="Local delivery object directory (development)"
    )
    manifest.add_argument(
        "--delivery-bucket", help="Cloud Storage delivery bucket name"
    )
    manifest.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the complete manifest without publishing it",
    )
    manifest.add_argument(
        "--message-mode", choices=["off", "events-only"]
    )
    manifest.add_argument(
        "--message-processor", choices=["disabled", "local", "zdr"]
    )
    manifest.set_defaults(handler=handler("lifecycle", "command_manifest", writer=True))

    sync = subparsers.add_parser(
        "sync", help="Harvest, derive, consolidate, and publish one manifest"
    )
    sync.add_argument("--db", help="Override the private SQLite database path")
    sync.add_argument("--date", default=date.today().isoformat())
    sync.add_argument(
        "--mode",
        choices=["initialize", "refresh", "daily", "latest"],
        default="daily",
    )
    sync.add_argument("--model", default=DEFAULT_MODEL)
    sync.add_argument(
        "--message-mode",
        choices=["off", "events-only"],
        help="Inbox privacy mode (default: AG_SCHOOL_MESSAGE_MODE or off)",
    )
    sync.add_argument(
        "--message-processor",
        choices=["disabled", "local", "zdr"],
        help="Events-only processor (default: disabled)",
    )
    sync.add_argument(
        "--message-concurrency",
        type=int,
        choices=range(1, 5),
        default=2,
        help="Concurrent ZDR message requests (default: 2; maximum: 4)",
    )
    sync.add_argument(
        "--progress",
        choices=["auto", "plain", "off"],
        default="auto",
        help="Progress on stderr (auto for interactive terminals)",
    )
    sync.add_argument("--delivery-root", help="Local delivery object directory")
    sync.add_argument("--delivery-bucket", help="Cloud Storage delivery bucket name")
    sync.set_defaults(handler=handler("lifecycle", "command_sync", writer=True))

    item = subparsers.add_parser(
        "item", help="Acknowledge, complete, dismiss, or restore a derived item"
    )
    item.add_argument(
        "item_action", choices=["acknowledge", "complete", "dismiss", "restore"]
    )
    item.add_argument("item_id", type=int)
    item.add_argument("--db", help="Override the private SQLite database path")
    item.set_defaults(handler=handler("lifecycle", "command_item", writer=True))

    db = subparsers.add_parser(
        "db", help="Pull, query and check a read-only copy of the cloud database"
    )
    db_subparsers = db.add_subparsers(dest="db_command", required=True)
    db_pull = db_subparsers.add_parser(
        "pull", help="Download the current cloud database unless it is unchanged"
    )
    db_pull.add_argument("--bucket", help="Private state bucket (default: AG_PRIVATE_BUCKET)")
    db_pull.add_argument("--project", help="Google Cloud project that owns the bucket")
    db_pull.add_argument("--db", help="Where to keep the copy")
    db_pull.set_defaults(handler=handler("database", "command_db_pull"))
    db_query = db_subparsers.add_parser("query", help="Run a saved read-only query")
    db_query.add_argument("name", choices=saved_query_names())
    db_query.add_argument("term", nargs="?", help="Search term, for `trace`")
    db_query.add_argument("--db", help="Database copy to read (default: the pulled copy)")
    db_query.add_argument("--json", action="store_true", help="Print rows as JSON")
    db_query.set_defaults(handler=handler("database", "command_db_query"))
    db_check = db_subparsers.add_parser("check", help="Run the invariant checks on a copy")
    db_check.add_argument("--db", help="Database copy to check (default: the pulled copy)")
    db_check.set_defaults(handler=handler("database", "command_db_check"))

    profile = subparsers.add_parser(
        "profile", help="Show or write the household's profile.toml and school.toml"
    )
    profile_subparsers = profile.add_subparsers(dest="profile_command", required=True)
    profile_show = profile_subparsers.add_parser(
        "show", help="Print the profile in use and which files it came from"
    )
    profile_show.set_defaults(handler=handler("records", "command_profile_show"))
    profile_write = profile_subparsers.add_parser(
        "write", help="Write school.toml and profile.toml to the state directory"
    )
    profile_write.add_argument("--host", required=True, help="The school's SkoleIntra host")
    profile_write.add_argument("--portal", default="parent", help="The portal in the site's URLs (default parent)")
    profile_write.add_argument("--child", action="append", required=True,
                               help="NAME:STUDENT_ID:CALENDAR_CLASS; repeat for each child")
    profile_write.add_argument("--force", action="store_true", help="Replace files that exist")
    profile_write.set_defaults(handler=handler("skolekit.cli", "command_profile_write"))
    week = subparsers.add_parser(
        "week", help="The validated week view: checked facts and bookings for the coming days")
    week.add_argument("--from", dest="from_date", help="The first day (ISO date); today by default")
    week.add_argument("--days", type=int, default=7, help="How many days (1 to 31, default 7)")
    week.add_argument("--child", help="Only this child (and the whole household)")
    week.add_argument("--include-undated", action="store_true", help="Also facts with no date")
    week.add_argument("--db", help="The database to read, such as a pulled copy (read-only)")
    week.set_defaults(handler=handler("records", "command_week"))

    scan = subparsers.add_parser(
        "scan", help="One scan with every stage the cloud job runs, on this machine (the local runtime)")
    scan.add_argument("--model", help="The model for derivation, review checks and the board (default: config)")
    scan.add_argument("--message-mode", choices=["off", "events-only"], help="Default: AG_SCHOOL_MESSAGE_MODE, else off")
    scan.add_argument("--message-processor", choices=["disabled", "local", "zdr"],
                      help="Default: AG_SCHOOL_MESSAGE_PROCESSOR, else disabled")
    scan.set_defaults(handler=handler("runtime", "command_scan"))

    serve_board = subparsers.add_parser(
        "serve", help="The browser board on this machine, for the local runtime (loopback only)")
    serve_board.add_argument("--port", type=int, default=8787)
    serve_board.set_defaults(handler=handler("runtime", "command_serve"))

    # `schedule` is the live lessons command (skolekit); this one schedules the scans.
    schedule_parser = subparsers.add_parser(
        "scan-schedule", help="The scan times as a launchd job (macOS) or systemd timer (Linux)")
    schedule_parser.add_argument("schedule_command", choices=["show", "install", "remove"])
    schedule_parser.set_defaults(handler=handler("runtime", "command_schedule"))

    runtime = subparsers.add_parser("runtime", help="Where and how the household runs (skoleklar.toml)")
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_show = runtime_commands.add_parser("show", help="The runtime, scan times, weather and GCP settings")
    runtime_show.add_argument("--json", action="store_true")
    runtime_show.set_defaults(handler=handler("runtime", "command_runtime_show"))

    serve = subparsers.add_parser(
        "mcp", help="Serve the checked records from a pulled copy as MCP tools over stdio ([mcp] extra)")
    serve.add_argument("--db", help="The copy to read (default: the pulled copy)")
    serve.add_argument("--pull", action="store_true",
                       help="Run `db pull` first; an unchanged copy is not downloaded again")
    serve.add_argument("--bucket", help="Private state bucket, for --pull (default: AG_PRIVATE_BUCKET)")
    serve.add_argument("--project", help="Google Cloud project that owns the bucket, for --pull")
    serve.add_argument("--event-confirmations", help="The owner's categorical event confirmations, as the board uses")
    serve.set_defaults(handler=handler("database", "command_mcp"))
    # The live reads, from skolekit, with the household's profile.
    add_live_commands(subparsers, load=load_site_profile)

    school = subparsers.add_parser("school", help="How the school's rules fit its stored pages")
    school_subparsers = school.add_subparsers(dest="school_command", required=True)
    school_check = school_subparsers.add_parser(
        "check", help="Count, per rule, the stored items it read, missed and refused (counts only)")
    school_check.add_argument("--db", help="The database to read (read-only)")
    school_check.set_defaults(handler=handler("records", "command_school_check"))

    discover = subparsers.add_parser(
        "discover", help="Capture sanitized request and response metadata"
    )
    discover.add_argument("--url", help="Default: the profile's login page")
    discover.add_argument(
        "--duration", type=int, default=5, help="Seconds to observe after page load"
    )
    discover.add_argument(
        "--headed", action="store_true", help="Show the discovery browser window"
    )
    discover.set_defaults(handler=handler("discovery", "command_discover"))
    return parser
