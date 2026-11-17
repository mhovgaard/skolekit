Input:

- `calendar`: `hero` (date and day), the five `weekdays` and the `weekend` dates,
  computed from the shared timing policy. Before 15:00 on a school day the hero is
  that day; from 15:00 it is the next school day; from Friday 15:00 to Sunday noon
  it is the coming Monday.
- `context.items`: the facts, each with `ref`, `type` (homework, bring, event,
  schedule_change, substitute, parent_action, reminder, context, routine,
  lesson_preview), `child` (a child's name or `household`), `title`, `summary`,
  `effective_date`, `end_date`, `actionability` (required, recommended,
  informational), `urgency`, `confidence`, `needs_review`, and where they apply
  what the fact is: `category` (an event's `trip`, `party`, `meeting`, `birthday`
  or `other`), `moves_time`, `recurring`, `school_subject`, `teacher`,
  `bring_items`, `rain_gear`, `activity_kind` and `celebrant`; and
  `private_message_fact`, `cancelled`, `message_thread`, `birthday_notice` and
  `repeats`.
- `context.source_checks` and `context.freshness`: name a failed or missing source
  in `limitations`; never present missing data as an empty day.

Output: `hero`, `weekdays`, `omitted` and `limitations`. Code keeps each line's
`text` as you wrote it and draws its label, box, checkbox and colour from the
facts in its `refs`; it writes the weekend rows from the facts, and checks that
every fact is accounted for and sits on a date it covers. A reference on another
date is dropped from its line; an unplaced required fact for today or later is
added by code.

Line text examples, as the board will show them:

| Facts | Your `text` | On the board |
|---|---|---|
| required bring | `udetøj` | **Medbring** udetøj |
| recommended bring | `hinkesten` | **Valgfrit** hinkesten |
| homework, `school_subject` Matematik | `s. 86–87; lav s. 43` | ☐ **Mat.** s. 86–87; lav s. 43 |
| substitute ×3 periods | `Alberte · MAT 02` | **Vikar** Alberte · MAT 02 |
| event, `category` trip | `Kulturdag i teatret` | `TUR` Kulturdag i teatret |
| cancelled message event | `Fødselsdagsfest` | `FEST` **Aflyst** Fødselsdagsfest |
| deadline | `tilmeld samtale` | **Frist** tilmeld samtale |
