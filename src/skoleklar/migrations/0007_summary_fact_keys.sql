-- 0007 summary_fact_keys (day-summary-v3).
-- A day summary names the facts its headline retells, by stable key, so the board
-- leaves the plan line out when a line of that day already shows one of them,
-- without comparing words. Empty for summaries written before. See docs/processing.md.

ALTER TABLE day_summaries ADD COLUMN fact_keys_json TEXT NOT NULL DEFAULT '[]';
