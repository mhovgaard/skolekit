-- 0003 bookings (layer 4): each child's own appointments, with their state
-- and an append-only change log the database writes itself.
-- See docs/data-foundation.md §5 and docs/conference-bookings.md.

CREATE TABLE booking_transitions (
    from_state TEXT NOT NULL,
    to_state   TEXT NOT NULL,
    PRIMARY KEY (from_state, to_state)
) STRICT;

INSERT INTO booking_transitions VALUES
    ('open', 'booked'), ('open', 'missed'), ('open', 'cancelled'),
    ('missed', 'booked'), ('booked', 'cancelled'), ('booked', 'held'),
    ('cancelled', 'booked');

CREATE TABLE bookings (
    student_id        INTEGER NOT NULL REFERENCES children(student_id),
    form_key          TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('conference', 'health', 'event', 'sfo')),
    title             TEXT NOT NULL,
    state             TEXT NOT NULL CHECK (state IN ('open', 'booked', 'missed', 'cancelled', 'held')),
    starts_local      TEXT CHECK (starts_local IS NULL OR starts_local GLOB
                      '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]'),
    minutes           INTEGER NOT NULL DEFAULT 30 CHECK (minutes BETWEEN 5 AND 240),
    minutes_source    TEXT NOT NULL DEFAULT 'default'
                      CHECK (minutes_source IN ('notice', 'default', 'owner')),
    deadline          TEXT,
    notice_key        TEXT,
    window_event_id   INTEGER,           -- the calendar day whose hours hold starts_local
    source_version_id INTEGER REFERENCES record_versions(id),
    first_seen_run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
    updated_run_id    INTEGER REFERENCES harvest_runs(id),
    updated_by        TEXT NOT NULL CHECK (updated_by IN ('scan', 'owner', 'phone')),
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (student_id, form_key),
    CHECK (state NOT IN ('booked', 'held') OR starts_local IS NOT NULL)
) STRICT;

CREATE TABLE booking_changes (
    id                INTEGER PRIMARY KEY,
    student_id        INTEGER NOT NULL,
    form_key          TEXT NOT NULL,
    at                TEXT NOT NULL,
    by                TEXT NOT NULL CHECK (by IN ('scan', 'owner', 'phone')),
    run_id            INTEGER REFERENCES harvest_runs(id),
    from_state        TEXT,
    to_state          TEXT NOT NULL,
    from_starts_local TEXT,
    to_starts_local   TEXT,
    from_minutes      INTEGER,
    to_minutes        INTEGER,
    FOREIGN KEY (student_id, form_key) REFERENCES bookings(student_id, form_key)
) STRICT;

CREATE INDEX idx_booking_changes_booking ON booking_changes(student_id, form_key, id);

-- Only listed state moves; anything else stops the scan.
CREATE TRIGGER bookings_transition BEFORE UPDATE OF state ON bookings
WHEN OLD.state != NEW.state AND NOT EXISTS (
    SELECT 1 FROM booking_transitions WHERE from_state = OLD.state AND to_state = NEW.state)
BEGIN SELECT RAISE(ABORT, 'booking transition not allowed'); END;

-- The change log writes itself; code cannot forget it.
CREATE TRIGGER bookings_history_insert AFTER INSERT ON bookings BEGIN
    INSERT INTO booking_changes (student_id, form_key, at, by, run_id,
                                 to_state, to_starts_local, to_minutes)
    VALUES (NEW.student_id, NEW.form_key, NEW.updated_at, NEW.updated_by,
            NEW.updated_run_id, NEW.state, NEW.starts_local, NEW.minutes);
END;

CREATE TRIGGER bookings_history_update AFTER UPDATE ON bookings
WHEN OLD.state IS NOT NEW.state OR OLD.starts_local IS NOT NEW.starts_local
     OR OLD.minutes IS NOT NEW.minutes
BEGIN
    INSERT INTO booking_changes (student_id, form_key, at, by, run_id,
        from_state, to_state, from_starts_local, to_starts_local, from_minutes, to_minutes)
    VALUES (NEW.student_id, NEW.form_key, NEW.updated_at, NEW.updated_by, NEW.updated_run_id,
        OLD.state, NEW.state, OLD.starts_local, NEW.starts_local, OLD.minutes, NEW.minutes);
END;

CREATE TRIGGER bookings_never_deleted BEFORE DELETE ON bookings
BEGIN SELECT RAISE(ABORT, 'bookings are never deleted'); END;
CREATE TRIGGER booking_changes_no_update BEFORE UPDATE ON booking_changes
BEGIN SELECT RAISE(ABORT, 'booking_changes is append-only'); END;
CREATE TRIGGER booking_changes_no_delete BEFORE DELETE ON booking_changes
BEGIN SELECT RAISE(ABORT, 'booking_changes is append-only'); END;
