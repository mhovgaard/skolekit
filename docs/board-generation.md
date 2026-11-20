# Board generation

For maintainers changing fact selection, wording or rendering. The model
selects and summarizes facts; code builds the state and renderers lay it out.
Use the shared [rendering workflows](../rendering/README.md) for agent previews.

## Context, slots and state

1. `dashboard_context.export_context` reads the checked records, coverage and
   source time. Exact duplicate display facts are collapsed with a repeat
   count. Message facts use sanitized stored wording; raw message text,
   evidence, source URLs and opaque handles stay out of composition context.
2. The active `dashboard-v3` bundle asks for per-child/day slots containing
   fact references, short Danish text and a priority tag. The text is content
   only: no `Medbring`, `Valgfrit`, `Frist`, `Vikar`, `Aflyst`, `Besked` or `Lektie`,
   no box word and no subject for one subject's homework. Each omitted fact gets a
   reason. The strict schema is the contract.
3. `dashboard_build.build_state` writes the board state (schema 3). Each line
   has `text`, fact `refs`, and presentation from `present.present`: `kind`,
   `label`, `chip`, the homework `box` and its `subject`, `cancelled`,
   `hidden`, `rain_gear`, `retold` and the `urgent` date window. Code supplies
   dates, day/week labels, update stamps, inbox metadata, weekend rows and
   overflow. It uses `dashboard_rules.fact_text` for weekend rows, the days
   ahead and the plain board. It also replaces the model's wording when a line
   consists entirely of substitutes with named teachers. The substitute line
   joins the shortened teacher's name, subject and timetable class when present.
   It drops references placed outside their dates, checks omission reasons and
   restores facts whose omission is unjustified. Excess lines become `+N punkter mere`.
4. `dashboard_validation.validate_composition` checks structure, never wording:
   every reference names a context fact, every fact is accounted for once and sits
   on the line its audit names, each line looks as its facts say, a shown fact's
   date fits its row, a classmate's birth date never shows, a shown message event
   shows its bring-item, and every weekend activity is shown, shown cancelled or
   counted. A structural failure gets bounded retries with fixed reason feedback.
5. If composition cannot validate, `fallback_composition` builds a plain board
   from facts; its `health` says so (`enkel visning`). Fallback weeks are not
   cached, so a later scan can retry the model. If fallback also fails, publication
   fails explicitly and the previous timeline remains.

The model must not invent dates, obligations or a child's involvement. An
uncertain fact remains uncertain; a composition audit is not new evidence.
Review verdicts are applied before composition as described in
[processing](processing.md#review-checks).

## Omission reasons

| Tag | Meaning checked by code |
| --- | --- |
| `outside_week` | No displayed weekday covers the fact |
| `duplicate` | A shown fact for the same child is named by `same_as` |
| `uncertain` | Never holds: a flagged fact is shown, its line marked `(?)` |
| `capacity` | Its destination row is full |
| `other_class` | Audience judgement; requires semantic review in testing |
| `not_relevant` | Informational context rather than an obligation/change/event |
| `birthday_notice`, `weekend` | Code handles placement/exclusion itself |

Classmate birth-date notices (`category` `birthday`) are not invitations or plans.
Labels such as `Medbring`, `Valgfrit`, `Frist`, `Vikar` and `Aflyst` are drawn from
the line's facts by `present`. The prompt asks the model to leave labels out of
its text. Check for duplicate labels in the rendered board during acceptance.

The timetable reader supplies `lesson_class`. The `derive-v11` schema for
published prose, including weekly plans, has no `lesson_class` field. A
substitute's class can therefore be missing from a prose-derived line. Some
timetable subject strings already contain class text, so appending the
timetable class can repeat it.

## Health

The scan records `ok` or `degraded` with a fixed reason for stages it checks
(`skoleklar/health.py`). Review checks and day summary generation contribute
scan-wide status. Composition fallback, older facts, forecast failures and
errors attaching extras can affect individual frames. The status line names
missing parts, for example `Mangler: vejret, dagene frem`, or counts them when
more than two are missing. The phone brief and run summary report degradation
from the shared scan stages. Composition and frame health are recomputed on
each scan, including when it reuses a cached composition. An unreadable
composition cache is recorded for diagnostics and can cost another model call.

## Frame construction and verification

The [timeline](dashboard-timeline.md) reuses a week's composition but exports
context for each future frame time. Code attaches lesson slots, day summaries,
weather, later events and eligible battery metadata. Optional live-subject/topic
overlays are separate from the saved subject-free state.

The [parents' board](parents-board.md), [children's view](day-view.md) and
weekend cover share conventions. Validate the same state for both HTML and
e-ink; do not invent an HTML-only summary to hide a panel layout problem.
Use synthetic fixtures to inspect narrow layouts, cancellations, bring-items,
long labels, stale coverage, date rollover and more activities than rows.
Current semantic limits are in [event interpretation](dashboard-event-interpretation.md).
