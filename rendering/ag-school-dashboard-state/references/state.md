# Shared dashboard state v3

Both renderers consume the same UTF-8 JSON. The executable allowlist lives in
`src/skoleklar/dashboard_renderer.py` (`validate`) and `src/skoleklar/present.py`
(`validate_line`). For a new render, the input must be schema 3. The separately
versioned display timeline contains already rendered frames.
[Display timeline](../../../docs/dashboard-timeline.md#serving-a-frame)

```json
{
  "schema_version": 3,
  "week_label": "UGE 40",
  "updated_label": "DATA LØR 26. SEP. · 19.45",
  "freshness": {
    "source_updated_at": "2026-09-26T19:45:00+02:00",
    "composed_at": "2026-09-26T20:00:00+02:00",
    "expires_at": "2026-09-28T07:45:00+02:00",
    "status": "fresh"
  },
  "hero": {
    "state": "Næste skoledag", "day": "Mandag 28. sep.", "date": "2026-09-28",
    "children": [
      {"name": "Alfa", "lines": []},
      {"name": "Beta", "lines": []}
    ]
  },
  "weekdays": [
    {"date": "2026-09-28", "day": "Man. 28. sep.",
     "children": [{"name": "Alfa", "lines": []}, {"name": "Beta", "lines": []}], "shared": []},
    {"date": "2026-09-29", "day": "Tir. 29. sep.",
     "children": [{"name": "Alfa", "lines": []}, {"name": "Beta", "lines": []}], "shared": []},
    {"date": "2026-09-30", "day": "Ons. 30. sep.",
     "children": [{"name": "Alfa", "lines": []}, {"name": "Beta", "lines": []}], "shared": []},
    {"date": "2026-10-01", "day": "Tor. 1. okt.",
     "children": [{"name": "Alfa", "lines": []}, {"name": "Beta", "lines": []}], "shared": []},
    {"date": "2026-10-02", "day": "Fre. 2. okt.",
     "children": [{"name": "Alfa", "lines": []}, {"name": "Beta", "lines": []}], "shared": []}
  ],
  "weekend": [],
  "weekend_overflow": 0,
  "messages": {"checked": true, "bodies_retained": 0, "unread_count": 2, "items": []},
  "health": {"composition": {"status": "ok"}, "weather": {"status": "degraded", "reason": "timeout_error"}}
}
```

Each entry in `lines` or `shared` has wording, fact references, and presentation.

```json
{"text": "s. 52–53; lav s. 26", "refs": ["fact-4"], "kind": "homework", "label": "",
 "chip": "", "box": true, "subject": "Mat.", "cancelled": false, "hidden": false,
 "rain_gear": false, "retold": false, "unsure": false, "urgent": null}
```

- `text`: display wording without presentation labels. Code supplies wording
  for named substitutes, weekend rows, days ahead and the plain board. Other
  composed lines use the model's wording. The general validator allows up to
  100 characters, and the weekend builder uses a 48-character wording limit.
- `refs`: the context facts the line shows (`fact-N`, per export); empty only for
  the `+N punkter mere` line (`kind` `more`).
- The rest is `present.present(facts, day)`: `kind` (`homework`, `bring`,
  `optional`, `deadline`, `time_change`, `change`, `substitute`, `trip`, `party`,
  `meeting`, `event`, `info`, `routine`, `more`), the bold `label` (`Medbring`,
  `Valgfrit`, `Frist`, `Vikar`, `Aflyst` or empty), the blue `chip` (`TUR`, `FEST`,
  `MØDE`, `ARR.` or empty), the homework `box` and its bold short `subject`,
  `cancelled` (every fact behind it is), `hidden` (a weekly routine, never drawn),
  `rain_gear` (the line includes rain clothes), `retold` (the day's plan
  headline references one of these facts), `unsure` (a fact behind it is still
  flagged for review: the line ends in `(?)`; a line without the field reads as
  sure), and `urgent` (the dates
  `{"from", "to"}` on which it is yellow, or null). Composition validation
  recomputes the presentation from the referenced facts.

Production state contains exactly five `weekdays`, Monday through Friday, each
with both children in hero order and up to two `shared` lines. The hero has up to
five lines per child, a weekday three. Renderers draw schema 3 school-day states as
the [parents' board](../../../docs/parents-board.md): one row per child, the next
school day on the left and the four school days after it on the right.

Hero requires exactly two distinct children in fixed order. It represents the
current school day before 15:00 and the next school day from 15:00; from Friday at
15:00 through Sunday before 12:00 it prepares the coming Monday behind the weekend
panel.

`weekend` has at most two rows, written by code from the facts
([event interpretation](../../../docs/dashboard-event-interpretation.md)): its
`date`, its `day` label, `who` (a child or `Begge`) and one to three lines, the
activity first, then what to bring. During the weekend cover it holds only the
current weekend. `weekend_overflow` counts the activities that had no row.
Calendar birth-date notices never take a row.

`health` is optional and written by code: per stage (`composition`, `facts`,
`review_checks`, `day_summaries`, `lookahead`, `day_view`, `weather`, `battery`,
`composition_cache`) either `{"status": "ok"}` or `{"status": "degraded", "reason"}`
with a fixed lower-case reason. The status line names what is missing
(`Mangler: vejret`); see [board generation](../../../docs/board-generation.md#health).

Messages contain `checked`, zero `bodies_retained`, the aggregate `unread_count`,
and at most two metadata-only rows. Each row may contain:

- `participant`: a shortened `First I.` label;
- `children`: one or both displayed child names;
- `activity`: a short display date or time;
- `unread`: boolean;
- optional `new_thread`, `new_message`, and `latest_unretrieved` booleans;
- optional `new` boolean: the row's latest activity is from the last 24 hours, or
  from Friday 15:00 on while the weekend panel shows.

`messages.new_count` is the number of new inbox items in context and may exceed
the number of displayed rows. Code copies the totals and builds the display rows.

Never include message subjects, bodies, previews, quoted text, full surnames,
source URLs, raw IDs, stable keys or opaque handles. The one name a message fact
keeps is a birthday party's birthday child, by first name, in its title (`Claras
fødselsdag`; see [message privacy](../../../docs/message-harvesting.md#extraction-and-validation)).
The [live-subject overlay](live-subjects.md) is a separate render-time read; it
never changes this serialized state.

## Parents' board fields

All optional and written by code, never by the model:

- `ahead`: at most six days after the composed week, from the day after the next
  school day to the fourth school day after it (Monday to Friday; a Saturday or
  Sunday in between only when it has facts), each `{"date", "children": [{"name",
  "lines"}], "shared": [LINE]}` with at most five lines per child and two shared.
- `later_events`: at most six `{"date", "who", "line"}` after the fourth school day
  ahead and up to fourteen days after the next school day, where `who` is a child's
  name or `Begge` and the line's `kind` is `trip`, `party`, `meeting`, `event` or
  `deadline` and its text at most 60 characters.
- `device`: `{"battery_percent": 0–100}` from the board's own status report.
- `countdown`: `{"lead", "text", "flag"}`, the one countdown line for the frame's
  day (`"45"` `"dage til juleaften"`, `"Tillykke,"` `"Alfa!"`), each 1–40
  characters, `flag` true for a birthday. Never the household's dates
  ([the countdown](../../../docs/parents-board.md#countdown)).

## Review count

`review_count` is no longer written: a fact flagged for review is shown on its
line, marked `unsure`, after the [review checks](../../../docs/processing.md#review-checks)
have settled what they can. A state written earlier may still carry the count
(0–99); it validates and is never drawn.

## Day summaries

`day_summaries` is optional. It is never composed by the
model: the cloud sync attaches it to every frame from the stored
[weekly-plan day summaries](../../../docs/processing.md#day-summaries). The parents' board
shows the next school day's `headline` as each child's plan line on the left, and
each school day ahead's `headline` as the first line of that day on the right; the
day view below shows the summary for its date.

```json
"day_summaries": [
  {
    "date": "2026-09-28",
    "children": [
      {"name": "Alfa", "status": "ok", "headline": "Bogstavet E og hinkebaner",
       "text": "I dansk møder du edderkoppen Egon. Du tegner edderkoppen og skriver E. I idræt tegner I hinkebaner. Du må gerne tage en hinkesten med."},
      {"name": "Beta", "status": "not_published", "headline": null, "text": null}
    ]
  }
]
```

- One entry per shown school day, at most nine, one per date: each `date` is one
  of `weekdays[].date` or one of the four school days after the next school day
  (`parents_board.lookahead_dates`), which may fall in the following week.
- `children` has both children in hero order.
- `status` is `ok`, `no_content`, `not_published` or `unavailable`. Only `ok`
  carries a `headline` (at most 48 characters) and a single-line `text` (at
  most 480 characters, usually 150–300); otherwise both are null.

## Day view

`day_view` is optional, never composed by the model and never
present with `weather`. The cloud sync attaches it from the stored calendar
lesson slots during the [children's windows](../../../docs/day-view.md). While it
is present both renderers draw the children's day view instead of the parents'
board, using the hero's lines, `day_weather` and `day_summaries` for its date.

```json
"day_view": {
  "date": "2026-09-28",
  "label": "I dag",
  "slots": ["09:00", "10:00", "10:45", "12:15", "13:00"],
  "children": [
    {"name": "Alfa", "lessons": [
      {"start": "09:00", "end": "10:45", "subject": "Dansk", "change": "vikar", "note": "Mette"},
      {"start": "12:15", "end": "13:45", "subject": "Idræt", "change": null, "note": ""}
    ]},
    {"name": "Beta", "lessons": [
      {"start": "09:00", "end": "09:45", "subject": "Musik", "change": "aflyst", "note": ""}
    ]}
  ]
}
```

- `date` is a school day and `label` is `I dag` or `I morgen`.
- `slots` is the day's bell schedule: one to ten ordered `HH:MM` starts, shared
  by both rows.
- `children` has both children in hero order, each with at most ten lessons in
  order. A lesson starts on a slot and ends after it.
- `subject` is display text of at most 32 characters; it may contain soft hyphens.
- `change` is null, `vikar` or `aflyst`. Only `vikar` has a `note`: the
  substitute's first name, at most 24 characters.

## Weekend weather

`weather` is optional. It is never composed by the model:
`skoleklar.weather` fetches one Open-Meteo forecast for the configured place and
attaches the object after composition, from Friday 15:00 until Sunday 12:00 (on
Saturday from 18:00 and on Sunday it shows Sunday and Monday, so there are always
two days). While it is present both renderers switch
to the weekend layout: the left panel shows the daytime weather cover,
`weekend` activities and the inbox; the right panel shows the weather
instead of the school-week table. A failed forecast leaves the normal layout.

```json
"weather": {
  "place": "Byen",
  "verdict": "Udedag",
  "reason": "Tørt med sol, 9–19°. Godt vejr til at være ude.",
  "label": "Bedste tid ude",
  "note": "Cykeltur: søn. 13–17 · lyst til 18:37",
  "days": [
    {
      "date": "2026-10-03", "day": "Lør. 3. okt.", "sunrise": "07:16", "sunset": "18:40",
      "icon": "partly", "low": 12, "high": 19, "rain_mm": 0,
      "cells": [
        {"label": "Formiddag", "icon": "cloud", "temp": 15, "rain_mm": 0},
        {"label": "Efterm.", "icon": "partly", "temp": 19, "rain_mm": 0}
      ],
      "flags": [{"icon": "wind", "label": "9 m/s"}],
      "hours": "ssccccsscsssscc",
      "window": {"start": 14, "end": 18, "label": "Tør · 18–19°"}
    }
  ]
}
```

- `days`: two entries, Saturday and Sunday or Sunday and Monday (one if the forecast
  stops at Sunday). The verdict only judges the weekend days.
- Day-level `icon`, `low`, `high` and `rain_mm` describe 09:00–16:00 local time
  for the left-hand cover, regardless of refresh time. The icon reflects prevailing
  conditions; a brief shower does not dominate, but thunderstorms remain explicit.
  Older bundles without day-level `rain_mm` use the half-day totals as a fallback.
- `cells`: exactly two half-days (08–12 and 12–18) with an icon (`sun`, `partly`,
  `cloud`, `rain`, `snow`, `storm`, `fog`), the highest temperature and whole mm of rain.
- `flags`: at most two per day, in order of severity: `storm`, `wind` (9 m/s or
  more), `umbrella` (1 mm or more), `frost`, `uv` (5 or more).
- `hours`: one letter per hour from 06 to 20: `s` sun, `c` cloud, `r` rain or snow.
- `window`: the sunniest dry stretch in daylight (four hours, else three or two), or
  `null`. Evaluate the full daylight period, including hours before `composed_at`:
  refreshing later must not erase an earlier dry trip. The day-level verdict,
  flags and cycling recommendation also use the full waking day, not only remaining hours.
- `verdict`: `Udedag`, `Indedag`, `Blandet`, `Stranden`, `Skoven` or `Museum`. With
  `AG_WEATHER_MODE=private` a rideable window makes it `Cykeldag` and the timeline
  label `Bedste cykeltid`; in family mode the same window is only the `note`.

Configuration is environment-only: `AG_WEATHER_PLACE`, `AG_WEATHER_LATITUDE`,
`AG_WEATHER_LONGITUDE` (no default: without them the board shows no forecast;
coordinates are rounded to two decimals before the request), `AG_WEATHER_MODE` (`family` or `private`) and
`AG_WEATHER=off` to disable it.

Freshness timestamps require timezones. Status is `fresh`, `stale`, `partial` or
`unknown`. Expiry follows source update time. Stale rendering suppresses the
apparently current inbox count and warns visibly while keeping the last facts.
