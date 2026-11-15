# Data foundation

For maintainers changing storage or writing read-only diagnostic queries.
The migration files are the authoritative schema; this document explains
ownership and invariants without duplicating their SQL.

## Operation

One household at one school/login owns one SQLite database,
`school-restricted.db`. It lives in the private state directory locally and
at `state/restricted-v1/school-restricted.db` in the private GCP bucket.
Do not combine school hosts in one file or mount the database through GCS FUSE.

A scan works on a local copy under the runtime lock. It applies pending
migrations, captures changes, rebuilds typed sources and bookings, runs
processing, then checks invariants and SQLite integrity before commit.
Local replacement uses fingerprint checks and staged file renames; cloud
replacement uses object-generation preconditions. The database is committed
before independent phone/board publication. See [architecture](architecture.md).

Writer connections enable foreign keys and WAL. New typed tables use SQLite
`STRICT`; the bundled runtime must support the applied migrations, including
`ALTER TABLE ... DROP COLUMN`. Read-only access never creates or migrates a file.
The private directory is mode 0700 and database files are mode 0600.

## Layers and repositories

| Layer | Main tables/views | Owner |
| --- | --- | --- |
| Reference | `children`, `schema_migrations`, `booking_transitions` | Profile/configuration and migrations |
| Capture | `harvest_runs`, `source_checks`, `records`, `record_versions`, `run_observations`, `changes` | `store/capture.py` |
| Typed sources | `src_forms`, `src_calendar_events`, `src_calendar_event_levels`, `src_notices`, `src_notice_days`, `src_refreshes` | `store/typed.py` and `foundation.py` |
| Interpretation | `derivations`, `derived_items`, `consolidations`, `consolidated_items`, review verdicts | `store/facts.py`, triage stages |
| Message facts | Restricted inbox projection, extraction receipts/attempts and `message_event_facts` | `store/message_facts.py` |
| Day summaries | `day_summary_runs`, `day_summaries` | `store/summaries.py` |
| Household state | `bookings`, `booking_changes`, `item_workflow`, `item_archive` | `bookings.py`, fact/delivery repositories |
| Read views | `v_live_facts`, `v_current_facts`, `v_trace`, `v_bookings` | Migrations and read services |

`storage.SnapshotStore` is the facade combining the repositories. Typed tables
are rebuilt from current captured versions and configured school wording.
`src_refreshes` records parser version, accepted/refused rows and the harvest.
The `children` table is filled from the private profile, not code constants.

## Identity and history

Captured records use `(student_id, source_type, source_key)` as identity.
See [source identities](data-sources.md#identity-and-coverage). Each record has
three hashes: raw representation, meaningful content and workflow/state.
An unchanged record reuses its version. Raw-only changes are audit noise;
content and state changes are distinguished.

Coverage controls absence. One authoritative absence marks a record `missing`,
a second marks it `removed`; seeing it again restores `active`. A failed or
bounded page check must not remove unseen history. Original source versions
remain available for traceability.

Derivations and consolidations retain source/member links and input/prompt
hashes. Only current complete live derivations feed current facts; validation
backfills do not silently supersede them. `item_workflow` keeps delivery,
acknowledgement, completion and dismissal by semantic stable key.
`item_archive` separately holds archival decisions and review state.

Messages never enter `records`/`record_versions`. Their index is a replaceable
current projection, not an append-only copy of the inbox. See
[message privacy](message-harvesting.md) for its narrower retention rules.

## Bookings

`bookings.py` joins configured booking forms to relevant notices and class
calendar windows. The household meaning configuration chooses form kinds and
default duration. A calendar conference window is not itself a booked slot.

A booking is keyed by student and form key. States are `open`, `booked`,
`missed`, `cancelled` and `held`; allowed transitions are in
`booking_transitions`. A past booked time becomes `held`. A disappearing
future booked time can cancel the booking. Triggers record state/time/duration
changes in `booking_changes` and reject illegal transitions. The invariant
checks reject overlapping booked slots for one child.

`week` and the saved `bookings` query expose bookings. A generic owner-command
inbox, owner booking-edit API and full booking-specific board/phone routing
are not implemented. Do not bypass that gap with unreviewed production SQL.

## Migrations

Numbered SQL files in [migrations](../src/skoleklar/migrations/) are applied
transactionally with their checksum in `schema_migrations`. The current series
covers the baseline, typed sources, bookings, read views and separation of
`item_archive` from delivery workflow state.

Never edit a recorded migration. Add a new number and tests for upgrade,
rollback-on-failure, foreign keys, triggers and invariants. The runner refuses
unknown future versions or modified recorded checksums. A baseline upgrade is
validated against schema shape; a database containing legacy message source
records is refused before use or migration.

## Invariants

[`invariants.py`](../src/skoleklar/invariants.py) owns the checks. They cover
schema/version agreement, relationships between source/typed rows, source
liveness, configured children and booking consistency. The scan also performs
SQLite integrity checking and WAL checkpointing before committing.
A failed invariant logs check names, not private offending rows, and prevents
commit. Keep each new check deterministic and test a failure case.

## Read-only queries

```sh
uv run --extra gcp skoleklar db pull --project PROJECT_ID
uv run skoleklar db query last-scan
uv run skoleklar db query bookings
uv run skoleklar db query trace SEARCH_TEXT
uv run skoleklar db check
uv run skoleklar week --db ~/.config/ag-skoleintra/cloud/school-restricted.db
```

| Saved query | Answers |
| --- | --- |
| `bookings` | What each child has booked, and what is open or missed |
| `trace TERM` | Where a fact came from; `TERM` is a stable key or words from its title or summary |
| `last-scan` | What the last scan changed, by child and source |

`--json` prints rows as JSON; any other read-only SQL works on the same copy.
For a capture, `skoleklar diff --db COPY --run-id N` shows meaningful changes
(`--include-noise` adds raw-only ones) and `skoleklar structure --db COPY` reports
sizes and counts without private content. Both open the file as a writer, so
give them a scratch copy, not the household database or the copy MCP serves.

`db pull` keeps a private generation-marked copy and skips unchanged downloads.
Queries and MCP calls open it with `mode=ro&immutable=1`; a pull replaces the
whole file and the next read sees it. An old copy is refused rather than
migrated. A household database that may be mid-write uses ordinary read-only
access instead. Query output may contain private school facts; keep it outside
tracked docs and public issue reports.
