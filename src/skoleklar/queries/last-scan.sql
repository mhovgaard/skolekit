-- What the last finished scan changed, by child and source. `raw_changed` is
-- left out: the source's bytes moved but nothing it says did, so no version.
SELECT run.id AS run, substr(run.started_at, 1, 16) AS started_utc, run.status,
       r.child, r.source_type, c.change_type, count(*) AS items
FROM harvest_runs run
JOIN changes c ON c.run_id = run.id
JOIN records r ON r.id = c.record_id
WHERE run.id = (SELECT max(id) FROM harvest_runs WHERE status IN ('complete', 'partial'))
  AND c.change_type NOT IN ('unchanged', 'raw_changed')
GROUP BY run.id, r.child, r.source_type, c.change_type
ORDER BY r.child, r.source_type, c.change_type;
