# The children's day view

For maintainers changing the child-facing lesson view and its frame timing.

Before school and in the afternoon before a school day, the panel shows the
children their day instead of the parents' board. It uses the board's colours
and type, with one row per child. `tests_python/test_day_view.py` renders it in
a real browser and as a panel frame.

## When it shows

| Window (Europe/Copenhagen) | Shows |
|---|---|
| School days 00:00–08:30 | Today, headed `I dag` |
| Sunday–Thursday 15:00–19:00 | The next school day, headed `I morgen` |

The parents' board shows the rest of the time, and the weekend panel from
Friday 15:00 until Sunday 12:00 as before. `day_view.view_date` holds the rule.
The windows add two layout changes per school day (08:30 and 19:00) and Sunday
15:00 and 19:00 to the [timeline](dashboard-timeline.md); the board wakes for
each, so a school day costs two more panel refreshes. A day on which neither child
has a lesson slot (a holiday the timing policy does not know) keeps the parents'
board.

Set `AG_DAY_VIEW=off` on the sync job to keep the parents' board all day.

## What it shows

Left, as at the weekend:

- `I DAG` or `I MORGEN`, the school day's weather glyph and temperature range;
- the weekday and date;
- the season's [house artwork](weekend-artwork.md), chosen from that day's weather;
- **Husk**: what to wear for the weather (`Jakke, hue og regntøj`), then each
  child's lines from the board's hero for that day, read from each line's kind:
  an optional item ends `(hvis du vil)`, homework starts `Lektie:`, and a
  substitute (its lesson tile shows it) or a weekly routine is left out; code's
  own markers (`Medbring:`, `Besked:`) are trimmed. The same item for both
  children is one `Begge` item, and the day's shared row is added with its
  `shared_kind`. Only a state without kinds is read from its markers.

Right, for each child:

- the name and `Fri kl. 13.45` after the last lesson that takes place, on the
  straw marker when cancelled lessons make the day end earlier than its timetable
  (yellow is for time urgency only, as on the [parents' board](parents-board.md));
- the lessons as tiles on the day's bell schedule. Both rows share the columns,
  so lessons at the same time line up. A substitute's tile is headed
  `Vikar Mette`; a cancelled lesson has a heavy outline, is struck through and
  headed `Aflyst`;
- the [weekly plan's day summary](processing.md#day-summaries), when one is written.
  Its first sentence is the child's own part of the day when the plan names the
  child (`Du og Emma laver en bogstavhistorie med F og H`); a text too long for
  the row is cut with `…` at its end, so that sentence stays.

## Where the lessons come from

Code builds the view from the calendar's `LessonsEvents` slots that the latest
harvest saw (`SnapshotStore.lesson_slots`); no model call is involved.
`day_view.day_lessons` then:

- keeps one lesson per slot: a `STØTTE` or `SPE` support lesson beside the
  class's own lesson gives way to it, and a changed copy wins over an unchanged one;
- marks a substitute only when `substituteStaff` differs from the scheduled
  teacher, the derivation's rule, and shows the first name only;
- marks a cancellation from the cancelled flags or `aflyst` in the title;
- merges one subject's lessons across a break of at most 15 minutes, so
  09:00–09:45 and 10:00–10:45 Dansk is one tile. Lunch keeps them apart;
- names the subject (`DAN` → `Dansk`, `N/T` → `Natur/teknologi`). Soft hyphens
  let long names break inside a narrow tile.

Unknown subject codes are shown as given. Add mappings only from known school
wording, with synthetic tests; an unfamiliar code is not a guessed subject.

## Layout

The panel measures each name with the real font and gives each slot column what
its tiles need; spare width is shared evenly, and a full day shrinks every column
and uses short names (`Mat.`). The browser uses weighted grid fractions from a
generous width estimate, so a column always fits the panel and a name that does
not fit breaks at its soft hyphen. Phones show the tiles wrapped instead of on
the schedule. Old data shows the browser's usual warning line, which can make
the browser panel a few pixels taller than 5:3.

## State

The cloud sync attaches `day_view` to a frame after the day summaries
(`runtime.scan._frame_extras`) when the frame's moment is in a window, its hero
shows that day and a child has lessons. The field is allowlisted by
`day_view_render.validate_day_view`; see the
[state contract](../rendering/ag-school-dashboard-state/references/state.md#day-view).
