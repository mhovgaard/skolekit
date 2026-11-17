The source layer is harvested school content. The derived layer contains atomic,
family-relevant facts; it is not a summary of every sentence in the source.

## Streams

| Value | Meaning |
| --- | --- |
| `homework` | Work the child must complete outside class. |
| `actions` | Something the child or parent must or may do, including things to bring. |
| `calendar` | A dated event or material exception that belongs on a timeline. |
| `context` | Useful information without a concrete action. |
| `watch` | A recurring or uncertain fact worth monitoring but not publishing as an event. |

## Item types

| Value | Meaning |
| --- | --- |
| `homework` | Explicit work to complete or prepare outside class. |
| `bring` | A physical item or clothing needed or optionally requested. |
| `event` | A concrete occurrence, meeting, trip, activity, closure, or social event. |
| `schedule_change` | An explicit exception to or replacement of the normal plan. |
| `substitute` | A substitute teacher or temporary staffing change. |
| `parent_action` | A reply, registration, payment, form, consent, or other parent task. |
| `reminder` | A time-relevant reminder that is not better represented by another type. |
| `context` | A useful one-off fact with no action. |
| `routine` | A stable recurring practice or expectation. |
| `lesson_preview` | What the class expects to cover; never homework without an explicit home task. |

## What an item is

| Field | Values | Meaning |
| --- | --- | --- |
| `category` | `trip`, `party`, `meeting`, `other`, null | The kind of `event`: an outing away from school, a celebration, a parent meeting or conference, or another occurrence. A `parent_action` is `meeting` when it means attending one, else null. Every other type is null. |
| `moves_time` | true, false, null | For a `schedule_change`: whether it moves when the child's day or an activity starts or ends. A moved room, a swapped lesson or another teacher is false. Every other type is null. |
| `recurring` | true, false | An event the class has every week (morning singing, weekly swimming), which the family needs no reminder of unless it is cancelled. |

The board and the phone use these fields as they are; they never re-read the
title to work out what an item is.

## Timing

`effective_date` is the day an item is due, needed, or occurs—not the message date
unless those are the same. `end_date` is only the final day of a multi-day item.
`complete_before_source_day` means homework shown under a weekday is ready before
that school day. `needed_on_source_day` is for bring-items. `no_derived_date`
requires both dates to be null. `ambiguous` requires review.

## Actionability and urgency

`required` needs explicit obligatory wording. `recommended` is genuinely optional.
`informational` asks for no action. Urgency describes consequence plus time, not
novelty. `routine` is normal information, `attention` matters soon, `urgent` needs
prompt action, and `critical` is reserved for immediate safety or severe same-day
consequences. Ambiguous facts use `needs_review`; review is not itself urgency.

## Identity and evidence

Each output item represents one fact or action. `dedupe_hint` names the underlying
semantic fact consistently across rewordings. `child` is a supplied child name or
`household` only when the source explicitly applies to both children. Every item
must contain a short verbatim evidence quote present in the supplied source.
