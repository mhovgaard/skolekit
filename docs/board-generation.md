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
3. `dashboard_build.build_state` writes the board state (schema 3). Every line is
   an object: the model's `text` as written, the `refs` of the facts it shows, and
   the presentation `present.present` computes from those facts: `kind`, the bold
   `label`, the category `chip`, the homework `box` and its `subject`, `cancelled`,
   `hidden` (a weekly routine), `rain_gear` and the `urgent` window. Code supplies
   dates, day/week labels, update stamps, inbox metadata, weekend rows and
   overflow; where it writes a line itself (weekend rows, the days ahead, the plain
   board) it words it from the fact's typed fields (`dashboard_rules.fact_text`).
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
| `uncertain` | The fact is flagged for review |
| `capacity` | Its destination row is full |
| `other_class` | Audience judgement; requires semantic review in testing |
| `not_relevant` | Informational context rather than an obligation/change/event |
| `birthday_notice`, `weekend` | Code handles placement/exclusion itself |

Classmate birth-date notices (`category` `birthday`) are not invitations or plans.
Labels such as `Medbring`, `Valgfrit`, `Frist`, `Vikar` and `Aflyst` are drawn from
the line's facts by `present`; they are never in the text.

## Health

A stage that fails without stopping the scan records a health entry in every frame
(`skoleklar/health.py`): the review checks, the day summaries (generated or
attached), the children's view, the weather, the days ahead, the battery, the
composition cache, the plain board and facts on an older contract. Each is `ok`, or
`degraded` with a fixed reason (an exception's type, never its message). The
status line of every view then names what is missing, `Mangler: vejret, dagene frem
· Opdateret 08/11 19.30`, in bold; the phone brief adds the same line for the stages
that ran before it, and the run summary lists the degraded ones. The composition
cache is recorded but not named on the board: a missing cache only costs a model call.

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
