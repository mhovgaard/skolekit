-- Where a fact or board line came from. :term is a fact's stable key or words
-- from its title or summary, e.g. `skoleklar db query trace hundrede`.
SELECT f.child, f.effective_date AS day, f.title,
       CASE WHEN f.archived_at IS NULL THEN 'current' ELSE 'archived' END AS fact,
       t.source_type, t.source_key,
       substr(first_run.started_at, 1, 16) AS first_seen_utc,
       substr(t.version_observed_at, 1, 16) AS version_seen_utc
FROM v_live_facts f
JOIN v_trace t ON t.item_id = f.id
LEFT JOIN harvest_runs first_run ON first_run.id = t.first_seen_run_id
WHERE f.stable_key = :term
   OR f.title LIKE '%' || :term || '%'
   OR f.summary LIKE '%' || :term || '%'
ORDER BY f.effective_date, f.child, f.title, t.source_type;
