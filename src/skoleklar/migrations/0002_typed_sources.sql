-- 0002 typed sources (layer 2) and the children they belong to (layer 0).
-- Layer 2 is a pure function of the current record versions and the parser
-- version: each scan deletes and rewrites it in one transaction, so it needs
-- no history of its own (layer 1 has it). See docs/data-foundation.md §3–§5.

CREATE TABLE children (
    student_id  INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    class_level INTEGER NOT NULL CHECK (class_level BETWEEN 0 AND 10),
    class_code  TEXT NOT NULL CHECK (length(class_code) = 2)   -- '02', as in calendar invitees
) STRICT;

-- One row per rebuild of layer 2: which scan, which parser, what was left out.
CREATE TABLE src_refreshes (
    run_id         INTEGER PRIMARY KEY REFERENCES harvest_runs(id),
    parser_version TEXT NOT NULL,
    refreshed_at   TEXT NOT NULL,
    row_counts     TEXT NOT NULL,          -- JSON object: table -> rows written
    rejected_count INTEGER NOT NULL CHECK (rejected_count >= 0)
) STRICT;

CREATE TABLE src_forms (
    student_id   INTEGER NOT NULL REFERENCES children(student_id),
    form_key     TEXT NOT NULL CHECK (form_key GLOB '[a-z]*:[0-9]*'),
    version_id   INTEGER NOT NULL REFERENCES record_versions(id),
    type_label   TEXT NOT NULL,            -- 'Samtale', 'Arrangement'
    title        TEXT NOT NULL,
    list_state   TEXT NOT NULL CHECK (list_state IN ('open', 'closed')),
    status_text  TEXT NOT NULL,            -- 'Min tid: 9. nov. 2026 15:00'
    booked_local TEXT CHECK (booked_local IS NULL OR booked_local GLOB
                 '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]'),
    answered_on  TEXT CHECK (answered_on IS NULL OR answered_on GLOB
                 '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),   -- 'Besvaret d. 24. aug. 2026'
    deadline     TEXT CHECK (deadline IS NULL OR deadline GLOB
                 '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    PRIMARY KEY (student_id, form_key)
) STRICT;

CREATE TABLE src_calendar_events (
    student_id            INTEGER NOT NULL REFERENCES children(student_id),
    event_id              INTEGER NOT NULL,  -- the event's originalId, the same in both feeds
    title                 TEXT NOT NULL,
    description           TEXT NOT NULL,
    starts_local          TEXT NOT NULL CHECK (starts_local GLOB
                          '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]'),
    ends_local            TEXT NOT NULL CHECK (ends_local >= starts_local),
    all_day               INTEGER NOT NULL CHECK (all_day IN (0, 1)),
    school_version_id     INTEGER REFERENCES record_versions(id),     -- SchoolEvents copy
    activities_version_id INTEGER REFERENCES record_versions(id),     -- ActivitiesEvents copy
    PRIMARY KEY (student_id, event_id),
    CHECK (school_version_id IS NOT NULL OR activities_version_id IS NOT NULL)
) STRICT;

-- The class levels an event is for: its invitees' class codes ('02') and the
-- classes in its title ('Badminton 1 & 2 klasse'). A school-wide event lists
-- every code from '00' to '09'; no rows means the source doesn't say.
CREATE TABLE src_calendar_event_levels (
    student_id  INTEGER NOT NULL,
    event_id    INTEGER NOT NULL,
    class_level INTEGER NOT NULL CHECK (class_level BETWEEN 0 AND 10),
    PRIMARY KEY (student_id, event_id, class_level),
    FOREIGN KEY (student_id, event_id)
        REFERENCES src_calendar_events(student_id, event_id) ON DELETE CASCADE
) STRICT;

CREATE TABLE src_notices (
    student_id   INTEGER NOT NULL REFERENCES children(student_id),
    notice_key   TEXT NOT NULL,
    version_id   INTEGER NOT NULL REFERENCES record_versions(id),
    title        TEXT NOT NULL,
    signup_from  TEXT CHECK (signup_from IS NULL OR signup_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
                                         -- 'Tilmelding: 16. okt. 2026 til 6. nov. 2026'
    signup_until TEXT CHECK (signup_until IS NULL OR signup_until GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    expires_on   TEXT CHECK (expires_on IS NULL OR expires_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),   -- 'Udløbsdato:'
    minutes      INTEGER CHECK (minutes BETWEEN 5 AND 240),   -- 'varer ca. 15 minutter'
    PRIMARY KEY (student_id, notice_key)
) STRICT;

CREATE TABLE src_notice_days (            -- 'Dato: 5. nov. 2026, 9. nov. 2026'
    student_id INTEGER NOT NULL,
    notice_key TEXT NOT NULL,
    day        TEXT NOT NULL CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    PRIMARY KEY (student_id, notice_key, day),
    FOREIGN KEY (student_id, notice_key)
        REFERENCES src_notices(student_id, notice_key) ON DELETE CASCADE
) STRICT;

CREATE INDEX idx_src_calendar_events_day ON src_calendar_events(student_id, starts_local);
CREATE INDEX idx_src_notices_signup ON src_notices(student_id, signup_until);
