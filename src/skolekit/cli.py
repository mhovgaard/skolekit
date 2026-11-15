"""`skolekit`: the live view of one child's school on SkoleIntra.

Each command reads the current bounded view for one child and prints typed
JSON. The profile and session live under `SKOLEKIT_HOME`, else
~/.config/skolekit. A product mounts the same services with its own state
namespace. Live reads store no school records.

Exit codes: 0 success; 2 usage, profile or policy; 3 no session or it has
expired (renew it with `skolekit auth login`); 4 the site didn't answer as
expected.

"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from skolekit import live
from skolekit.auth.credentials import KeychainCredentials
from skolekit.auth.session import SessionStore, school_client, session_is_valid
from skolekit.parsers.messages import MessageParseError
from skolekit.parsers.weekly_plan import WeeklyPlanParseError
from skolekit.profile import Child, ProfileError, SiteProfile, load_profile, parse_children, write_profile
from skolekit.school import parse_school
from skolekit.sources.base import CollectionError
from skolekit.state import state_dir
from skolekit.transport import ReadOnlyViolation, SessionExpired

EXIT_USAGE, EXIT_SESSION, EXIT_SITE = 2, 3, 4
# The list each read returns, printed one item per line with --format ndjson.
ITEMS = {"schedule": "lessons", "calendar": "events", "weekly_plan": "days", "forms": "forms",
         "notices": "notices", "messages": "threads", "message_thread": "messages"}

Load = Callable[[], SiteProfile]


def load_files() -> SiteProfile:
    """The toolkit's profile: both files from the state directory, no defaults."""
    return load_profile(state_dir())


def _child(profile: SiteProfile, wanted: str | None) -> Child | None:
    try:
        return live.choose_child(profile.children, wanted)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return None


def ics(result: dict[str, Any], host: str, timezone: str) -> str:
    """The calendar's events as an iCalendar file, in the school's local time."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//skolekit//live calendar//EN"]
    for event in result["events"]:
        start, end = event["starts_local"], event["ends_local"] or event["starts_local"]
        if not start:
            continue
        lines += ["BEGIN:VEVENT", f"UID:{event['event_id']}@{host}"]
        if event["all_day"]:
            lines += [f"DTSTART;VALUE=DATE:{start[:10].replace('-', '')}"]
        else:
            stamp = lambda value: value.replace("-", "").replace(":", "") + "00"  # noqa: E731
            lines += [f"DTSTART;TZID={timezone}:{stamp(start)}", f"DTEND;TZID={timezone}:{stamp(end)}"]
        summary = event["title"].replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
        lines += [f"SUMMARY:{summary}", "END:VEVENT"]
    return "\r\n".join([*lines, "END:VCALENDAR"]) + "\r\n"


def _print(name: str, result: dict[str, Any], output: str, school: Any) -> None:
    if output == "ndjson" and name in ITEMS:
        for item in result[ITEMS[name]]:
            print(json.dumps({"child": result["child"], **item}, ensure_ascii=False))
    elif output == "ics":
        sys.stdout.write(ics(result, school.host, school.timezone))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def run_read(args: argparse.Namespace, name: str, load: Load, **options: Any) -> int:
    """Load the profile and session, run one live read for one child, print the result."""
    try:
        profile = load()
    except ProfileError as error:
        print(f"Invalid profile: {error}", file=sys.stderr)
        return EXIT_USAGE
    child = _child(profile, getattr(args, "child", None))
    if child is None:
        return EXIT_USAGE
    if getattr(args, "week", None):
        try:
            options["week"] = live.Week.parse(args.week, live.now(child).date())
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return EXIT_USAGE
    state = SessionStore().load()
    if state is None:
        print("No saved session. Run `skolekit auth login` first.", file=sys.stderr)
        return EXIT_SESSION
    try:
        with school_client(state, profile.school.host) as client:
            result = live.READS[name](client, child, **options)
    except SessionExpired as error:
        print(f"{error}: run `skolekit auth login`.", file=sys.stderr)
        return EXIT_SESSION
    except ReadOnlyViolation as error:
        print(f"Refused: {error}", file=sys.stderr)
        return EXIT_USAGE
    except (CollectionError, MessageParseError, WeeklyPlanParseError, httpx.HTTPError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_SITE
    _print(name, result, getattr(args, "format", "json"), profile.school)
    return 0


def _common(formats: Sequence[str] = ("json", "ndjson")) -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--child", help="The child's name or student ID; needed when the profile has several")
    common.add_argument("--format", choices=list(formats), default="json")
    return common


def add_live_commands(subparsers: argparse._SubParsersAction, *, load: Load) -> None:
    """The live reads: messages, schedule, calendar, forms and notices.

    `skolekit` mounts these with its files; a product mounts them with its own
    defaults. Each reads the current view unless asked for more.
    """
    common = _common()

    messages = subparsers.add_parser("messages", help="The inbox, live: subjects and IDs, or one thread's text")
    commands = messages.add_subparsers(dest="messages_command", required=True)
    listing = commands.add_parser("list", parents=[common],
                                  help="Threads with subject, IDs, unread state and latest activity; no text")
    listing.add_argument("--pages", type=int, default=1, help="Inbox pages to read (default 1)")
    listing.add_argument("--unread", action="store_true", help="Only threads with unread messages")
    listing.set_defaults(handler=lambda args: run_read(args, "messages", load, pages=args.pages,
                                                       unread_only=args.unread))
    reading = commands.add_parser("read", parents=[common],
                                  help="The last messages of one thread, with their text; read live, never stored")
    reading.add_argument("thread", type=int, help="The thread ID from `messages list`")
    reading.add_argument("--last", type=int, default=1, help="How many of the latest messages (default 1)")
    reading.add_argument("--pages", type=int, default=5, help="Inbox pages to look for the thread on")
    reading.set_defaults(handler=lambda args: run_read(args, "message_thread", load, thread_id=args.thread,
                                                       last=args.last, pages=args.pages))

    schedule = subparsers.add_parser("schedule", parents=[common],
                                     help="The week's lessons, with substitutes and cancellations")
    schedule.add_argument("--week", help="46, 2026-W46 or a date in the week; the current week by default")
    schedule.set_defaults(handler=lambda args: run_read(args, "schedule", load))

    calendar = subparsers.add_parser("calendar", parents=[_common(("json", "ndjson", "ics"))],
                                     help="The week's events for the child's class and the whole school")
    calendar.add_argument("--week", help="46, 2026-W46 or a date in the week; the current week by default")
    calendar.add_argument("--all-classes", action="store_true", help="Also events only for other classes")
    calendar.set_defaults(handler=lambda args: run_read(args, "calendar", load, all_classes=args.all_classes))

    forms = subparsers.add_parser("forms", parents=[common],
                                  help="Forms with their status, booked time and deadline")
    forms.add_argument("--state", choices=["open", "closed"], help="Only open or only closed forms")
    forms.set_defaults(handler=lambda args: run_read(args, "forms", load, state=args.state))

    notices = subparsers.add_parser("notices", parents=[common],
                                    help="The front page's notices and the dates they state; expired ones left out")
    notices.add_argument("--pages", type=int, default=1, help="Front-page pages to read (default 1)")
    notices.add_argument("--include-expired", action="store_true", help="Also notices past their expiry")
    notices.set_defaults(handler=lambda args: run_read(args, "notices", load, pages=args.pages,
                                                       include_expired=args.include_expired))


def command_school_show(args: argparse.Namespace, load: Load) -> int:
    try:
        school = load().school
    except ProfileError as error:
        print(f"Invalid profile: {error}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps({"host": school.host, "portal": school.portal, "locale": school.locale,
                      "timezone": school.timezone, "class_codes": list(school.class_codes),
                      "class_label": school.class_label, "rules": school.digest()}, indent=2))
    return 0


def command_auth_status(args: argparse.Namespace, load: Load) -> int:
    state = SessionStore().load()
    result: dict[str, Any] = {"saved": state is not None, "valid": None}
    if state is not None and args.check:
        try:
            result["valid"] = session_is_valid(state, load().login_url)
        except ProfileError as error:
            print(f"Invalid profile: {error}", file=sys.stderr)
            return EXIT_USAGE
    print(json.dumps(result))
    return 0 if result["saved"] and result["valid"] is not False else EXIT_SESSION


def command_auth_login(args: argparse.Namespace, load: Load) -> int:
    from skolekit.auth.browser import browser_login  # Playwright only when logging in

    try:
        url = load().login_url
    except ProfileError as error:
        print(f"Invalid profile: {error}", file=sys.stderr)
        return EXIT_USAGE
    return asyncio.run(browser_login(url, timeout=args.timeout, use_keychain=args.use_keychain,
                                     auto_submit=args.auto_submit, headless=args.headless))


def command_profile_write(args: argparse.Namespace) -> int:
    """Write school.toml and profile.toml for one school and its children."""
    rows = []
    for value in args.child:
        parts = value.split(":")
        if len(parts) != 3 or not parts[1].isdigit():
            print(f"--child takes NAME:STUDENT_ID:CALENDAR_CLASS, such as Alfa:9101:001; got {value!r}",
                  file=sys.stderr)
            return EXIT_USAGE
        rows.append({"name": parts[0], "student_id": int(parts[1]), "calendar_class_name": parts[2]})
    try:
        school = parse_school({"site": {"host": args.host, "portal": args.portal}})
        children = parse_children({"children": rows}, school)
        paths = write_profile(SiteProfile(school, children), state_dir(), force=args.force)
    except ProfileError as error:
        print(str(error), file=sys.stderr)
        return EXIT_USAGE
    for path in paths:
        print(f"wrote {path}")
    return 0


def command_profile_show(args: argparse.Namespace, load: Load) -> int:
    try:
        profile = load()
    except ProfileError as error:
        print(f"Invalid profile: {error}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps({
        "state_dir": str(state_dir()), "school": profile.school.host, "files": list(profile.sources),
        "children": [{"name": child.child, "student_id": child.student_id,
                      "class": profile.school.label(child.class_level) if child.class_level is not None else None}
                     for child in profile.children],
    }, ensure_ascii=False, indent=2))
    return 0


def command_credentials(args: argparse.Namespace) -> int:
    """Save, check or delete the school login in the macOS Keychain; the password is typed by the parent."""
    keychain = KeychainCredentials()
    if args.credentials_command == "status":
        saved = keychain.school_login() is not None
        print(json.dumps({"keychain_credentials": saved}))
        return 0 if saved else EXIT_USAGE
    if args.credentials_command == "delete":
        keychain.delete()
        print("The school login is removed from the Keychain.")
        return 0
    username = input("SkoleIntra username: ").strip()
    password = getpass.getpass("SkoleIntra password: ")
    if not username or not password:
        print("The username and the password can't be empty.", file=sys.stderr)
        return EXIT_USAGE
    keychain.save(username, password)
    print("The school login is saved in the Keychain.")
    return 0


def command_mcp(args: argparse.Namespace, load: Load) -> int:
    """Serve the live reads as MCP tools over stdio, until the client closes it."""
    try:
        from skolekit.mcp.server import build_server
    except ImportError:
        print("The MCP server needs the mcp extra: `uv sync --extra mcp`.", file=sys.stderr)
        return EXIT_USAGE
    try:
        load()
    except ProfileError as error:
        print(f"Invalid profile: {error}", file=sys.stderr)
        return EXIT_USAGE
    build_server(load).run("stdio")
    return 0


def build_parser(load: Load = load_files) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skolekit", description="The live, read-only view of a child's SkoleIntra")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile = subparsers.add_parser("profile", help="The school and the children the toolkit reads for")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("show", help="The profile in use").set_defaults(
        handler=lambda args: command_profile_show(args, load))
    write = profile_commands.add_parser("write", help="Write school.toml and profile.toml to the state directory")
    write.add_argument("--host", required=True, help="The school's SkoleIntra host, such as <school>.m.skoleintra.dk")
    write.add_argument("--portal", default="parent", help="The portal in the site's URLs (default parent)")
    write.add_argument("--child", action="append", required=True,
                       help="NAME:STUDENT_ID:CALENDAR_CLASS as the site's URLs and lessons calendar show them; "
                            "repeat for each child")
    write.add_argument("--force", action="store_true", help="Replace files that exist")
    write.set_defaults(handler=command_profile_write)

    school = subparsers.add_parser("school", help="The school's settings and how its rules fit its pages")
    school_commands = school.add_subparsers(dest="school_command", required=True)
    school_commands.add_parser("show", help="The school in use and its rules' digest").set_defaults(
        handler=lambda args: command_school_show(args, load))
    check = school_commands.add_parser(
        "check", parents=[_common(("json",))],
        help="Per rule, counts of this week's live items it read, missed and refused; never text")
    check.set_defaults(handler=lambda args: run_read(args, "school_check", load))

    auth = subparsers.add_parser("auth", help="The saved school session")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    status = auth_commands.add_parser("status", help="Whether a session is saved, and with --check whether it works")
    status.add_argument("--check", action="store_true", help="Ask the school whether the session still works")
    status.set_defaults(handler=lambda args: command_auth_status(args, load))
    login = auth_commands.add_parser("login", help="Sign in in a private browser and save the session")
    login.add_argument("--timeout", type=int, default=15, help="Minutes to wait for the sign-in")
    login.add_argument("--use-keychain", action="store_true")
    login.add_argument("--auto-submit", action="store_true")
    login.add_argument("--headless", action="store_true")
    login.set_defaults(handler=lambda args: command_auth_login(args, load))
    credentials = auth_commands.add_parser("credentials", help="The school login in the macOS Keychain")
    credentials.add_argument("credentials_command", choices=["set", "status", "delete"])
    credentials.set_defaults(handler=command_credentials)

    add_live_commands(subparsers, load=load)

    plan = subparsers.add_parser("weekly-plan", parents=[_common()],
                                 help="One week's plan, lesson by lesson; the current week if it is listed")
    plan.add_argument("--week", help="46, 2026-W46 or a date in the week")
    plan.set_defaults(handler=lambda args: run_read(args, "weekly_plan", load))

    mcp = subparsers.add_parser("mcp", help="Serve these reads as MCP tools over stdio ([mcp] extra)")
    mcp.set_defaults(handler=lambda args: command_mcp(args, load))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except ModuleNotFoundError as error:
        if (error.name or "").split(".")[0] != "playwright":
            raise
        print("Browser login needs the browser extra: `uv sync --extra browser`.", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
