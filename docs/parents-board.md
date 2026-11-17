# The parents' board

For maintainers changing the family overview. This page defines selection,
layout and emphasis; it is not a record of any household's deployment.

The board replaces the school-week table on the panel and in the browser
whenever neither the [weekend cover](../rendering/ag-school-dashboard-state/references/state.md#weekend-weather)
nor the [children's day view](day-view.md) shows: the evening before a school day
from 19:00, school days 08:30–15:00, and Sunday 12:00–15:00.

Each child has a row across the panel, and the dividers line up on both halves.

| Part | Shows |
|---|---|
| Top left (dark) | The next school day's date and a weather heads-up: `1–5° Vintertøj`, `9–14° Regntøj` |
| Top right | `Opdateret 08/11 19.30` and the board's battery, with `ÆLDRE DATA`, `Mangler: …` and `N punkter at tjekke` when they apply |
| Child row, left | The weekly plan's headline for the day, homework with a checkbox, what to bring, the time-urgent line, the latest message |
| Child row, right | The four school days after the next school day as dated rows, each led by the weekly plan's headline for that day, then events up to two weeks out below a dotted rule |
| `Begge` row | The unread total (`4 ulæste i alt`) on the left; what concerns both children on the right |

`parents_board_render.py` draws it on the panel and writes the same layout as
HTML, so the two never disagree about what is shown.

## What each line is

Every line of the board state is an object: its wording, the facts it shows by
reference, and how it looks, which `present.present` computes from those facts
([board generation](board-generation.md#context-slots-and-state)). Its `kind` is
one of `homework`, `bring`, `optional`, `deadline`, `time_change`, `change`,
`substitute`, `trip`, `party`, `meeting`, `event`, `info`, `routine` or `more`.

A fact says what it is ([the fact model](processing.md#the-fact-model)): its
`category`, whether a change `moves_time`, whether an event is `recurring`, its
`school_subject`, its `bring_items` and whether they are `rain_gear`. The board
takes it at its word and never reads the wording. A fact without a category is a
plain event (`ARR.`), and a change that does not say it moves a time is not
yellow. Precedence for an event: the owner's confirmed category, then a message's
`activity_kind`, then the fact's `category`.

Every highlight that guides the eye comes from the line's presentation, never from
its words: the checkbox and the bold subject (`homework`, the subject in its short
form: `Mat.`, `Eng.`, `N/T`), the bold `Medbring`, `Valgfrit`, `Frist` and `Vikar`
labels, `Aflyst` for a cancelled line, the `TUR`/`FEST`/`MØDE`/`ARR.` boxes, the
yellow time-urgency marker and leaving out a weekly routine. A deadline gets
`Frist`, so a reply or a payment with a date reads as one. Substitute lines
with a named teacher use the stored teacher, subject and timetable class when
present, for example `Vikar Alex W. · MAT 02`. [Board generation](board-generation.md#context-slots-and-state)
describes when the builder writes a line itself.

- **The day's plan line** on the right is the weekly plan's
  [day summary](processing.md#day-summaries) headline (`Trekanter og bogstavet T`,
  `Bogstavhistorie med Emma: F og H`) in the serif, before the day's other lines,
  so a week without homework still says what each day holds. It is left out when
  the day has no summary, or when a line of the day shows a fact the headline
  retells (`retold`, from the summary itself): `TUR Kulturdag i teatret` is said once.
- **Homework** has a checkbox and a bold subject (`Mat.`, `Eng.`, `N/T`); two
  homework items of a day share a line at a tab stop, or right after each other
  when the first is long, and get a line each when they do not fit. The checkbox
  is a display marker. Tick off synced homework in Apple Reminders.
- **Trips, parties and meetings** get a blue box: `TUR`, `FEST`, `MØDE`, and `ARR.`
  for other events.
- **Yellow marks time urgency only**: a deadline, or a `time_change` (a change
  that moves when the day or an activity starts or ends: an early finish, a new
  meeting time), within two days of the frame. A moved room or lesson, homework and
  substitutes are never yellow, even when the line names a time.
- **Rain on an event day** shows as `☂ regn` under its date, unless the day's
  things to bring are rain gear (`rain_gear`).
- **Weekly routines** (kind `routine`: a `recurring` event, such as morning
  singing) are never shown and never take one of a day's lines (`hidden`); a
  cancellation is (`Morgensang aflyst`).

## Days ahead and later events

The days ahead are the four school days (Monday to Friday) after the next school
day (`parents_board.lookahead_dates`), so with the next school day on the left the
board always covers the next five school days and never runs out on a Thursday
evening or a Friday: Friday's board looks at Monday to Thursday. A Saturday or
Sunday in between gets a row only when something is on, such as a birthday party;
an empty weekend takes no room. Holidays on a weekday are not known and count as
school days.

The composed school week covers Monday to Friday. For a frame whose days ahead
reach past it (from Monday evening on), the sync job writes the missing days from
the facts, as the fallback board does (`ahead`), and attaches the next week's
[day summaries](processing.md#day-summaries) for their plan lines. Weekend
activities from the composed weekend rows replace those days' own rows. Trips,
parties, meetings and required deadlines after the fourth school day ahead, up to
fourteen days after the next school day, are `later_events`: one row each with its
date, box and title, `Begge` when both children or no particular child. A weekly event already shown in the days
ahead (morning singing) is not repeated there. Each frame's context for these reaches
18 days ahead (`parents_board.CONTEXT_DAYS`); the composition's own context is
unchanged.

A board for the next school day never lists the days before it: Monday's board
from Sunday 12:00 has no Sunday. Only when the weekend forecast fails, and the
parents' board shows during the weekend cover (Friday 15:00 to Sunday 12:00), are
the weekend's activities day rows before the look-ahead. An activity the weekend
rows had no room for is counted on the last one, `Fødselsdagsfest (+1 mere)`,
never as a line of its own; two activities both show when they fit.

## When a week does not fit

Nothing is cut mid-line. Whole items give way in this order, and only on the side
that makes a row tall; the row then says `+N punkter mere · +N senere`:

1. Substitutes and plain information on the days ahead, then on the next school day.
2. Things to bring on days without an event; tomorrow's list stays on the left.
3. Later events without a deadline.
4. The extra space between lines.
5. The plan lines of the days ahead that have other lines, one at a time, the
   farthest day first, from the child whose right side is tallest.
6. The plan line on the left, where the left side is the taller one.
7. The remaining plan lines of the days ahead, in the same way.
8. All later events.
9. Things to bring on event days, then tomorrow's bring list.
10. Last resort: the farthest day of the fullest row.

Plan lines retell the weekly plan and are no things to do, so they are not counted
in `+N punkter mere`.

Homework, deadlines, changed times and trips go only in the last step. The
homework-heavy test week (`tests_python/test_parents_board.py`: two subjects a day
for both children, a culture-day trip each and four later events) shows all 16
homework items with nothing cut, also with a plan line on every day ahead.

## Messages

Each child's latest message is one line: `Karen: Samtykke tur … 08/11`. A filled
envelope and a bold date mean unread. The sender is the first name only. The
one- or two-word topic needs the household's opt-in (`AG_MESSAGE_TOPICS=model`,
see [live subjects](../rendering/ag-school-dashboard-state/references/live-subjects.md));
without it, or when the topic is hidden, the line has sender and date only.

## The same conventions on every view

- **Status:** `Opdateret 07/11 09.23` and the battery at 14 px, bold with an
  `ÆLDRE DATA ·` prefix when the data is old (said once; no separate banner), and
  with `Mangler: …` when a part of the board is missing
  ([health](board-generation.md#health)). The
  parents' board has it top right, the other two bottom right, where their top
  right holds a heading.
- **Messages at the weekend:** each child's latest message as one line
  (`✉ Alfa · Clara: Fødselsdag … 07/11`, filled envelope when unread) and the
  total (`3 ulæste i alt`).
- **Weekend activities:** a category box (`FEST`, `TUR`, `MØDE`, `ARR.`),
  `Medbring` in bold, and an activity the rows had no room for counted on the
  last one, `(+1 mere)`.
- **Yellow** only for time urgency: the children's day view marks `Fri kl.` when
  the day ends early; substitutes and the weekend's `Ingen tør tur` are plain.
  The sun and rain colours in the weather drawings are weather, not emphasis.
- **Blue** only for category boxes: headings and labels (`WEEKEND`,
  `WEEKENDVEJR`, `I MORGEN`, `HUSK`, `TUR` in the sun's arc, `+2 mere`) are ink.
- **Edges:** the frame's white mat is the margin: 10 px at the outer edges, 14 px
  at the split.

## Battery

The sync job reads the board's latest status report from the control bucket and
adds `device.battery_percent` to every frame when the report is under 48 hours old
and has a battery. The panel draws it after the update stamp.
