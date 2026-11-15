"""Command helpers for records."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from skoleklar.brief import (
    brief_item_stable_keys,
    build_brief,
    build_brief_range,
    render_brief_range_text,
    render_brief_text,
)
from skoleklar.triage.derivation import OpenRouterHTTPError, OpenRouterClient
from skoleklar.delivery_manifest import delivery_items_from_snapshot
from skoleklar.restricted_messages import resolve_message_mode, resolve_message_processor
from skoleklar.household import snapshot_db_path
from skoleklar.cli.common import load_profile_or_report as _load_profile
from skoleklar.store.check import check_database
from skolekit.profile import CONFIG_FILES
from skoleklar.storage import SnapshotStore
from skoleklar.cli.common import config_root
from skoleklar.cli.auth import load_openrouter_api_key


def command_diff(args: argparse.Namespace) -> int:
    database = Path(args.db).expanduser().resolve() if args.db else snapshot_db_path()
    if not database.exists():
        print("No snapshot database. Run `uv run skoleklar harvest` first.", file=sys.stderr)
        return 2
    with SnapshotStore(database) as store:
        result = store.diff(args.run_id, include_noise=args.include_noise)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_structure(args: argparse.Namespace) -> int:
    database = Path(args.db).expanduser().resolve() if args.db else snapshot_db_path()
    if not database.exists():
        print("No snapshot database. Run `uv run skoleklar harvest` first.", file=sys.stderr)
        return 2
    with SnapshotStore(database) as store:
        result = store.structure_report(args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_derivation_report(args: argparse.Namespace) -> int:
    database = Path(args.db).expanduser().resolve() if args.db else snapshot_db_path()
    if not database.exists():
        print("No snapshot database.", file=sys.stderr)
        return 2
    since: date | None = None
    if args.since:
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            print("--since must be an ISO date such as 2026-08-01.", file=sys.stderr)
            return 2
    with SnapshotStore(database) as store:
        report = store.derivation_report(
            prompt_version=args.prompt_version,
            model=args.model,
            source_types=args.source,
            since=since,
            include_source_preview=args.include_source_preview,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_consolidation_report(args: argparse.Namespace) -> int:
    database = Path(args.db).expanduser().resolve() if args.db else snapshot_db_path()
    if not database.exists():
        print("No snapshot database.", file=sys.stderr)
        return 2
    with SnapshotStore(database) as store:
        report = store.consolidation_report(args.consolidation_id)
    if report is None:
        print("No completed consolidation found.", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_day_summaries(args: argparse.Namespace) -> int:
    from datetime import timedelta

    from skoleklar.day_summaries import generate_day_summaries, summary_lookup

    try:
        resolve_message_mode(getattr(args, "message_mode", None))
        today = date.fromisoformat(args.today) if args.today else date.today()
        shown = date.fromisoformat(args.show) if args.show else None
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    database = (
        Path(args.db).expanduser().resolve() if args.db else snapshot_db_path()
    )
    if not database.exists():
        print("No snapshot database. Run `uv run skoleklar harvest` first.", file=sys.stderr)
        return 2
    if shown is not None:
        # Prints the school week's stored summaries; makes no model call.
        with SnapshotStore(database, read_only=True) as store:
            children = [row["child"] for row in store.connection.execute(
                "SELECT DISTINCT child FROM records WHERE source_type = 'weekly_plan' ORDER BY child")]
            lookup = summary_lookup(store)
            monday = shown - timedelta(days=shown.weekday())
            days = []
            for offset in range(5):
                value = (monday + timedelta(days=offset)).isoformat()
                days.append({"date": value, "children": [
                    {"name": child, **lookup(child, value)} for child in children]})
        print(json.dumps({"day_summaries": days}, ensure_ascii=False, indent=2))
        return 0
    client = None
    if not args.dry_run:
        api_key = load_openrouter_api_key()
        if not api_key:
            print("No OpenRouter API key. Run `uv run skoleklar llm-credentials set`.",
                  file=sys.stderr)
            return 2
        client = OpenRouterClient(api_key=api_key, model=args.model)
    with SnapshotStore(database) as store:
        try:
            result = generate_day_summaries(store, client, model=args.model, today=today,
                                            limit=args.limit, dry_run=args.dry_run)
        except OpenRouterHTTPError as error:
            print(json.dumps({"error": f"provider_http_{error.status_code}"}))
            return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_archival_report(args: argparse.Namespace) -> int:
    database = Path(args.db).expanduser().resolve() if args.db else snapshot_db_path()
    if not database.exists():
        print("No snapshot database.", file=sys.stderr)
        return 2
    with SnapshotStore(database) as store:
        report = store.archival_report(args.archival_run_id)
    if report is None:
        print("No completed archival run found.", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_brief(args: argparse.Namespace) -> int:
    database = (
        Path(args.db).expanduser().resolve()
        if args.db
        else snapshot_db_path()
    )
    if not database.exists():
        print("No snapshot database. Run `uv run skoleklar harvest` first.", file=sys.stderr)
        return 2
    if args.to_date and not args.from_date:
        print("--to requires --from.", file=sys.stderr)
        return 2
    try:
        if args.from_date:
            start_date = date.fromisoformat(args.from_date)
            end_date = date.fromisoformat(args.to_date or args.from_date)
            if end_date < start_date:
                raise ValueError
            target_date = None
        else:
            target_date = date.fromisoformat(args.date or date.today().isoformat())
            start_date = end_date = None
    except ValueError:
        print(
            "Brief dates must be ISO dates and --to cannot be before --from.",
            file=sys.stderr,
        )
        return 2
    try:
        message_mode = resolve_message_mode(getattr(args, "message_mode", None))
        message_processor = resolve_message_processor(
            getattr(args, "message_processor", None)
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    with SnapshotStore(
        database
    ) as store:
        items = delivery_items_from_snapshot(store, message_mode=message_mode)
        if target_date is None:
            assert start_date is not None and end_date is not None
            brief = build_brief_range(
                items,
                start_date=start_date,
                end_date=end_date,
                compact=args.compact,
            )
        else:
            brief = build_brief(
                items,
                target_date=target_date,
                latest_run_id=store.latest_completed_run_id(),
            )
        brief["coverage"] = store.message_coverage(
            mode=message_mode, processor=message_processor
        )
        if args.record_delivery:
            store.record_delivery_keys(
                brief_item_stable_keys(brief["items"]),
                at=datetime.now(UTC).isoformat(),
            )
    if args.format == "json":
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    elif target_date is None:
        print(render_brief_range_text(brief))
    else:
        print(render_brief_text(brief))
    return 0


def command_profile_show(args: argparse.Namespace) -> int:
    """Print the profile the scan would use, and which files it came from."""
    profile = _load_profile()
    if profile is None:
        return 2
    home = config_root()
    print(json.dumps({
        "state_dir": str(home),
        "files": {name: ("read" if name in profile.sources else "absent")
                  for name in CONFIG_FILES},
        "school": {"host": profile.school.host, "portal": profile.school.portal,
                   "locale": profile.school.locale, "timezone": profile.school.timezone,
                   "class_codes": list(profile.school.class_codes),
                   "class_label": profile.school.class_label,
                   "rules": profile.school.digest()},
        "children": [{"student_id": child.student_id, "name": child.child,
                      "calendar_class_name": child.calendar_class_name,
                      "class_level": child.class_level, "class_code": child.class_code,
                      "class": profile.class_labels().get(child.child),
                      "department_ids": list(child.department_ids)}
                     for child in profile.children],
        "login_url": profile.login_url,
    }, ensure_ascii=False, indent=2))
    return 0


def command_week(args: argparse.Namespace) -> int:
    """The validated week view from the household's database (or a pulled copy), read-only."""
    from zoneinfo import ZoneInfo

    from skoleklar.week import week_view

    try:
        start = date.fromisoformat(args.from_date) if args.from_date else datetime.now(
            ZoneInfo("Europe/Copenhagen")).date()
    except ValueError:
        print("--from must be an ISO date such as 2026-11-16.", file=sys.stderr)
        return 2
    database = Path(args.db).expanduser().resolve() if args.db else snapshot_db_path()
    if not database.exists():
        print(f"No database at {database}; run a harvest or `skoleklar db pull` first.", file=sys.stderr)
        return 2
    # A copy (`--db`) is opened immutable, as `db query` and `skoleklar mcp` open it, so reading
    # leaves no -wal or -shm beside it. The household's own database may be mid-write: plain read-only.
    copy = args.db is not None and database != snapshot_db_path().resolve()
    try:
        with SnapshotStore(database, read_only=True, immutable=copy) as store:
            view = week_view(store, start=start, days=args.days, child=args.child,
                             include_undated=args.include_undated)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(view, ensure_ascii=False, indent=2))
    return 0


def command_school_check(args: argparse.Namespace) -> int:
    """Per rule of the school's wording, counts of the stored items it read; never text."""
    profile = _load_profile()
    if profile is None:
        return 2
    database = Path(args.db).expanduser() if args.db else snapshot_db_path()
    if not database.exists():
        print(f"No database at {database}; run a harvest first.", file=sys.stderr)
        return 2
    print(json.dumps(check_database(database, profile.school), indent=2))
    return 0
