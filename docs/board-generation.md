# Board generation

For maintainers changing fact selection, wording or rendering. The model
selects and summarizes facts; code builds the state and renderers lay it out.
Use the shared [rendering workflows](../rendering/README.md) for agent previews.

## Context, slots and state

1. `dashboard_context.export_context` reads the checked records, coverage and
   source time. Exact duplicate display facts are collapsed with a repeat
   count. Message facts use sanitized stored wording; raw message text,
   evidence, source URLs and opaque handles stay out of composition context.
2. The active `dashboard-v2` bundle asks for per-child/day slots containing
   fact references, short Danish text and a priority tag. Each omitted fact
   gets a reason. The strict schema is the contract.
3. `dashboard_build.build_state` supplies dates, day/week labels, update stamps,
   required markers, inbox metadata, weekend selection and overflow. It drops
   references placed outside their dates, checks omission reasons and restores
   facts whose omission is unjustified. Excess lines become a visible count.
4. `dashboard_validation.validate_composition` checks dates, reference/audit
   accounting, required provenance and event/bring semantics. A rule failure
   gets bounded retries with fixed reason feedback.
5. If composition cannot validate, `fallback_composition` builds a plain board
   from facts, marked `· enkel visning`. Fallback weeks are not cached, so a
   later scan can retry the model. If fallback also fails, publication fails
   explicitly and the previous timeline remains.

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

Classmate birth-date notices are not invitations or plans. Marker prefixes
such as `Besked:`, `Medbring:`, `Valgfrit:`, `Lektie:` and `Vikar:` are derived
from the line's facts, not freely authored by the model.

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
