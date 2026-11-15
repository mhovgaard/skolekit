-- 0004 views (layer 5): computed, nothing stored twice. A view lists its
-- columns when it is created, so a migration that adds a column to a table
-- below recreates the views that select it.

-- Every live fact from the newest complete derivation of current sources,
-- archived or not, with its workflow. Store.current_derived_items reads this,
-- so the code and ad hoc queries agree.
CREATE VIEW v_live_facts AS
SELECT i.*, d.harvest_run_id, d.model,
       w.first_delivered_at, w.last_delivered_at,
       w.acknowledged_at, w.completed_at, w.dismissed_at,
       w.archived_at, w.archive_reason, w.archive_run_id,
       w.archive_reviewed_at, w.archive_review_as_of,
       w.archive_review_reason,
       w.first_seen_at AS workflow_first_seen_at,
       w.last_seen_at AS workflow_last_seen_at
FROM derived_items i
JOIN derivations d ON d.id = i.derivation_id
LEFT JOIN item_workflow w ON w.stable_key = i.stable_key
WHERE d.status = 'complete'
  AND d.purpose = 'live'
  AND NOT EXISTS (
      SELECT 1
      FROM derivation_sources s
      JOIN records r ON r.id = s.record_id
      WHERE s.derivation_id = d.id
        AND (r.current_version_id != s.version_id OR r.status != 'active')
  )
  AND NOT EXISTS (
      SELECT 1
      FROM derivation_sources own_source
      JOIN derivation_sources newer_source
        ON newer_source.record_id = own_source.record_id
       AND newer_source.version_id = own_source.version_id
      JOIN derivations newer_derivation
        ON newer_derivation.id = newer_source.derivation_id
      WHERE own_source.derivation_id = d.id
        AND newer_derivation.status = 'complete'
        AND newer_derivation.purpose = 'live'
        AND newer_derivation.id > d.id
  );

-- The facts the board and phone start from.
CREATE VIEW v_current_facts AS
SELECT * FROM v_live_facts WHERE archived_at IS NULL;

-- Where a fact came from: fact -> source version -> item -> URL.
CREATE VIEW v_trace AS
SELECT i.id AS item_id, i.stable_key, i.child, i.title, i.effective_date,
       r.source_type, r.source_key, r.source_url, r.first_seen_run_id,
       v.id AS version_id, v.run_id AS version_run_id, v.observed_at AS version_observed_at
FROM derived_items i
JOIN derived_item_sources s ON s.item_id = i.id
JOIN records r ON r.id = s.record_id
JOIN record_versions v ON v.id = s.version_id;

CREATE VIEW v_bookings AS
SELECT c.name AS child, b.form_key, b.kind, b.title, b.state, b.starts_local, b.minutes,
       b.minutes_source, b.deadline, b.notice_key, b.window_event_id,
       b.updated_by, b.updated_at
FROM bookings b JOIN children c USING (student_id);
