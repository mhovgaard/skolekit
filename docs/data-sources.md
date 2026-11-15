# Source adapters

For maintainers adding a school or changing SkoleIntra parsing. Use synthetic
fixtures and configured wording; do not copy authenticated school pages into Git.

## Supported sources

Paths below are relative to the configured portal/student/child route.
`school.toml` supplies host, portal, locale and class wording; `profile.toml`
supplies the children, class identifiers and department IDs.

| Source | Reads | Current behavior |
| --- | --- | --- |
| Front page | `Index`, read-only `POST newsitems/showmore` | Notices with expiry, dates, stable feed IDs, text and links; cursor paging is bounded |
| Calendar | `calendareventsource/ActivitiesEvents`, `SchoolEvents`, `LessonsEvents` | Dated class/school events, lesson slots, staffing, substitutes and cancellations |
| Weekly plans | The child's `item/weeklyplansandhomework/list` route and selected plans | General and dated lesson blocks, plain text, structured fields and links |
| Forms | `forms/open`, `forms/closed`, GET detail/answer pages | Status, booked time, deadline, descriptions and question labels; no submission |
| Messages | `messages/conversations` and exact requested thread/message GET routes | Live reads or restricted opt-in processing; never captured message records |

The published-page `SOURCES` registry has four entries. Household harvesting
adds the restricted message path separately. `daily` bounds the calendar to
the previous week through two weeks ahead, reads two recent weekly plans and
one front page. Other presets and explicit bounds are in
[`harvest.py`](../src/skoleklar/harvest.py). A successful bounded scan does not
mean all historical pages were read.

## Authentication and read safety

Browser login saves a private cookie session. Ordinary reads use
[`ReadOnlyClient`](../src/skolekit/transport.py): configured HTTPS school host,
GET/HEAD and the declared read-only front-page POST. Redirects to login or
another host raise `SessionExpired`. School writes, including the calendar's
view-selection POST, form answer submission and message read-status endpoints,
are outside the allowlist.

Use `skoleklar probe` or the sanitized `discover` report when diagnosing a
changed page. Reports keep response shapes and redacted URLs, not cookies,
passwords or private response content. An expired session must be reported
rather than normalized into a zero-item source.

## Published-page parsing

Front-page `.sk-news-item` feed-type/feed-ID attributes identify notices.
Show-more requests carry per-feed cursors; stop on exhaustion, repeated cursors
or the scope's safety limit. A notice can link a form without becoming the same
source record. Cross-source fact consolidation happens later.

Forms have separate open/closed lists, using fields such as `Name`, `Status`,
`Deadline`, `Type` and `Url`. Stable identity ignores open/closed/edit path
variants. Read the list's booked-time status as configured wording; never infer
a child's booking from a class-wide calendar window.

Weekly plans embed JSON in `#root[data-clientlogic-settings-weeklyplansapp]`.
Keep general blocks and each weekday/subject block, preserving source text.
Teacher headings are hints, not classifications. Only a block classified as
homework inherits completion before its source day; bring-items are needed on
the day, and explicit timing overrides defaults. The authoritative model
contract is the active prompt schema, not an example JSON shape in this guide.

`LessonsEvents` is the staffing source. Preserve scheduled/absent/substitute
teachers and lesson metadata; a weekly-plan timetable alone cannot establish
a substitute. Grouping for the brief must retain the individual source members.
Pure routine lesson rows can be handled without a model; changed prose may
still contain an actionable exception.

## Identity and coverage

| Source | Stable identity |
| --- | --- |
| Front page | Feed type and feed ID |
| Forms | Form type and numeric ID |
| Calendar | Feed/source identity; lesson slots use stable slot content rather than regenerated display IDs |
| Weekly plans | Class/week/year key |
| Restricted messages | HMAC thread/message handles outside the source-version tables |

Adapters return `SourceCheck` with records, status and explicit coverage:
`full`, `page_window`, `date_window`, `record_keys`, `current_state` or
`excluded_by_policy`. A date window authorizes absence only inside that window;
a page window does not authorize removing older unseen records. Check both
success and failure coverage when changing an adapter.

The toolkit's live defaults are one child, current week/first page, that child's
class plus school-wide events and unexpired notices. `school check` reports
wording-rule counts, not private page text. The product's school check reads
stored pages instead of making another live request.

## Extending an adapter

1. Put school wording in locale/configuration rather than a hardcoded host or name.
2. Declare the read endpoints and bounded scope. Do not extend the transport
   allowlist without establishing that an endpoint only reads.
3. Preserve untouched published-page content, normalized meaning, stable IDs and
   separate raw/content/state hashes. Use the restricted path for messages.
4. Add invented fixtures and `httpx.MockTransport` checks for paging, lost login,
   deduplication, identity stability and failed/partial coverage.
5. Run source tests, `test_transport.py`, golden harvest and package boundaries.

Contact book, diaries, learning plans and notification endpoints may be visible
on a school's site, but they are not supported adapters. Treat them as new work,
not as sources a builder can enable with a configuration flag.

Weekend/day weather uses Open-Meteo separately. Only configured coordinates
rounded to two decimals are sent; no weather location means no request.
No school records or message text are sent to the forecast service.
