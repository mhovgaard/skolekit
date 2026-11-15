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
| Top right | `Opdateret 08/11 19.30` and the board's battery; `ÆLDRE DATA ·` and `N punkter at tjekke` come first when they apply |
| Child row, left | The weekly plan's headline for the day, homework with a checkbox, what to bring, the time-urgent line, the latest message |
| Child row, right | The four days after the next school day as dated rows, then events up to two weeks out below a dotted rule |
| `Begge` row | The unread total (`4 ulæste i alt`) on the left; what concerns both children on the right |

`parents_board_render.py` draws it on the panel and writes the same layout as
HTML, so the two never disagree about what is shown.

## What each line is

The composed week carries per line a `kinds` entry, written by code from the facts
behind the line (`parents_board.attach_kinds`, from the audit's locations):
`homework`, `bring`, `optional`, `deadline`, `change`, `substitute`, `trip`,
`party`, `meeting`, `event`, `info` or `more`. A composition cached before kinds
existed gets them from its audit on the next scan. Without kinds (an old state, a
local render) the renderer reads the markers (`Lektie:`, `Medbring:` …) and a
leading task verb instead.

- **Homework** has a checkbox and a bold subject (`Mat.`, `Eng.`, `N/T`); two
  homework items of a day share a line at a tab stop, or right after each other
  when the first is long, and get a line each when they do not fit.
- **Trips, parties and meetings** get a blue box: `TUR`, `FEST`, `MØDE`, and `ARR.`
  for other events. The box replaces the `Besked:` marker.
- **Yellow marks time urgency only**: a deadline, or a change that moves a time
  (`kl. 12`, `mødetid`, `fri`), within two days of the frame. A moved room or lesson,
  homework and substitutes are never yellow.
- **Rain on an event day** shows as `☂ regn` under its date, unless its packing
  list already names rain gear.
- **Weekly routines** (`Morgensang`, `Fællessang`) are never shown; a cancellation
  is (`Morgensang aflyst`).

## Days ahead and later events

The composed school week covers Monday to Friday. For a frame whose four days
ahead reach past it (Wednesday or Thursday evening), the sync job writes the
missing days from the facts, as the fallback board does (`ahead`). Weekend
activities from the composed weekend rows replace those days' own rows. Trips,
parties, meetings and required deadlines from five to fourteen days after the next
school day are `later_events`: one row each with its date, box and title, `Begge`
when both children or no particular child. A weekly event already shown in the days
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
5. The plan line, where the left side is the taller one.
6. All later events.
7. Things to bring on event days, then tomorrow's bring list.
8. Last resort: the farthest day of the fullest row.

Homework, deadlines, changed times and trips go only in the last step. The
homework-heavy test week (`tests_python/test_parents_board.py`: two subjects a day
for both children, a culture-day trip each and four later events) shows all 16
homework items with nothing cut.

## Messages

Each child's latest message is one line: `Karen: Samtykke tur … 08/11`. A filled
envelope and a bold date mean unread. The sender is the first name only. The
one- or two-word topic needs the household's opt-in (`AG_MESSAGE_TOPICS=model`,
see [live subjects](../rendering/ag-school-dashboard-state/references/live-subjects.md));
without it, or when the topic is hidden, the line has sender and date only.

## The same conventions on every view

- **Status:** `Opdateret 07/11 09.23` and the battery at 14 px, bold with an
  `ÆLDRE DATA ·` prefix when the data is old (said once; no separate banner). The
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
