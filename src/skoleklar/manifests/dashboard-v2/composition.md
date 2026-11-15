Input:

- `calendar`: `hero` (date and day), the five `weekdays` and the `weekend` dates,
  computed from the shared timing policy. Before 15:00 on a school day the hero is
  that day; from 15:00 it is the next school day; from Friday 15:00 to Sunday noon
  it is the coming Monday.
- `context.items`: the facts, each with `ref`, `type` (homework, bring, event,
  schedule_change, substitute, parent_action, reminder, context, routine,
  lesson_preview), `child` (a child's name or `household`), `title`, `summary`,
  `effective_date`, `end_date`, `actionability` (required, recommended,
  informational), `urgency`, `confidence`, `needs_review`, and where relevant
  `private_message_fact`, `cancelled`, `activity_kind`, `message_thread`,
  `birthday_notice` and `repeats`.
- `context.source_checks` and `context.freshness`: name a failed or missing source
  in `limitations`; never present missing data as an empty day.

Output: `hero`, `weekdays`, `omitted` and `limitations`. Code turns each line into
board text by adding the markers its facts require, writes the weekend rows from
the facts, and checks that every fact is accounted for and sits on a date it
covers. A line whose facts are on another date is dropped; an unplaced required
fact for today or later is added by code.

Line text examples, as code will show them:

| Facts | Your `text` | On the board |
|---|---|---|
| required bring, message | `udetøj` | `Besked: Medbring: udetøj` |
| recommended bring | `hinkesten` | `Valgfrit: hinkesten` |
| homework | `Læs s. 86–87; lav s. 43` | `Læs s. 86–87; lav s. 43` |
| substitute ×3 periods | `Alberte · MAT 02` | `Vikar: Alberte · MAT 02` |
| message event | `Klassearrangement` | `Besked: Klassearrangement` |
| cancelled message event (`cancelled`) | `Fødselsdagsfest` | `Besked: Fødselsdagsfest aflyst` |
