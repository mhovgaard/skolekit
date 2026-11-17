# Composition rules

The board is an editorial projection of fresh structured facts, not a second
database. Composition is judgement only; code writes everything that follows a
fixed rule. The versioned contract is
[`dashboard-v3`](../../../src/skoleklar/manifests/dashboard-v3/): its
`instructions.md` and `composition.md` are the rules the cloud model follows, and
this skill follows the same ones.

## Who writes what

| Composition (model or local agent) | Code (`dashboard_build`, `dashboard_rules`) |
|---|---|
| Which facts matter, in what order, per child and day | Dates, day labels, week label, update stamp |
| Which facts are the same thing (one line, several refs) | Each line's look from its facts (`present`): labels, boxes, checkbox, colour |
| Short Danish line text, content only | Named substitute wording, weekend rows and the overflow count |
| A priority tag per line | Inbox rows and counts, freshness |
| A reason tag for every left-out fact | Checking each reason tag; showing a certain fact whose tag does not hold |
| | `+N punkter mere` when a row is full; the to-check count |

## Selection and wording

1. Obligations first: homework, things to bring, deadlines and replies, then
   changes (cancellations, substitutes, changed times), then events and context.
2. Show the whole school week. A past day keeps its facts; the renderer folds
   distant days when space is short.
3. Optional stays optional and conditions stay attached. Never turn `må gerne`
   into an obligation; the board labels optional items `Valgfrit`.
4. Merge facts into one line only when they report the same thing. A calendar
   event under both children does not prove both classes are concerned.
5. Classmate birth dates (`birthday_notice`) are never the child's event: omit
   them. Weekend facts are placed by code: omit them with the tag `weekend`.
6. `needs_review` facts stay out unless the structured fields settle them; the
   board shows how many were left out.
7. Never invent a time, place or obligation, and never write relative time words
   (`i dag`, `i morgen`): the row gives the date.

Aim for 18–30 characters per line, without labels: `udetøj`, not `Medbring: udetøj`.
The hero holds five lines per child, a weekday three, plus one shared line per day.
