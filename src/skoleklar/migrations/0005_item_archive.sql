-- 0005 item_archive (phase 8 of docs/architecture-review-2026-11-07.md).
-- The archive stage's results lived in six item_workflow columns, next to the
-- board's delivery state. They move to a table of their own, by stable key.
-- v_live_facts reads them from there under the same names and in the same
-- order, so every reader sees the same rows and columns as before.

CREATE TABLE item_archive (
    stable_key     TEXT PRIMARY KEY REFERENCES item_workflow(stable_key),
    archived_at    TEXT,
    archive_reason TEXT,
    archive_run_id INTEGER REFERENCES archival_runs(id),
    reviewed_at    TEXT,
    review_as_of   TEXT,
    review_reason  TEXT
) STRICT;

INSERT INTO item_archive(stable_key, archived_at, archive_reason, archive_run_id,
                         reviewed_at, review_as_of, review_reason)
SELECT stable_key, archived_at, archive_reason, archive_run_id,
       archive_reviewed_at, archive_review_as_of, archive_review_reason
FROM item_workflow
WHERE archived_at IS NOT NULL OR archive_reason IS NOT NULL OR archive_run_id IS NOT NULL
   OR archive_reviewed_at IS NOT NULL OR archive_review_as_of IS NOT NULL
   OR archive_review_reason IS NOT NULL;

DROP VIEW v_current_facts;
DROP VIEW v_live_facts;

ALTER TABLE item_workflow DROP COLUMN archived_at;
ALTER TABLE item_workflow DROP COLUMN archive_reason;
ALTER TABLE item_workflow DROP COLUMN archive_run_id;
ALTER TABLE item_workflow DROP COLUMN archive_reviewed_at;
ALTER TABLE item_workflow DROP COLUMN archive_review_as_of;
ALTER TABLE item_workflow DROP COLUMN archive_review_reason;

-- Every live fact from the newest complete derivation of current sources,
-- archived or not, with its delivery state and its archive result.
CREATE VIEW v_live_facts AS
SELECT i.*, d.harvest_run_id, d.model,
       w.first_delivered_at, w.last_delivered_at,
       w.acknowledged_at, w.completed_at, w.dismissed_at,
       a.archived_at, a.archive_reason, a.archive_run_id,
       a.reviewed_at AS archive_reviewed_at, a.review_as_of AS archive_review_as_of,
       a.review_reason AS archive_review_reason,
       w.first_seen_at AS workflow_first_seen_at,
       w.last_seen_at AS workflow_last_seen_at
FROM derived_items i
JOIN derivations d ON d.id = i.derivation_id
LEFT JOIN item_workflow w ON w.stable_key = i.stable_key
LEFT JOIN item_archive a ON a.stable_key = i.stable_key
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
