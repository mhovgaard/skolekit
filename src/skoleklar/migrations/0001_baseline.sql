-- 0001 baseline: the schema as it stood before numbered migrations
-- (8 November 2026). A database created before then is marked as version 1
-- without running this file, once its tables match it (see migrations/__init__.py).

CREATE TABLE IF NOT EXISTS harvest_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS source_checks (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
    student_id INTEGER NOT NULL,
    child TEXT NOT NULL,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL,
    complete INTEGER NOT NULL,
    record_count INTEGER NOT NULL,
    error TEXT,
    metadata_json TEXT NOT NULL,
    UNIQUE(run_id, student_id, source_type)
);

CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    child TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_url TEXT NOT NULL,
    first_seen_run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
    last_seen_run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
    current_version_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    missing_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(student_id, source_type, source_key)
);

CREATE TABLE IF NOT EXISTS record_versions (
    id INTEGER PRIMARY KEY,
    record_id INTEGER NOT NULL REFERENCES records(id),
    run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
    observed_at TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    state_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    body_text TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_observations (
    run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
    record_id INTEGER NOT NULL REFERENCES records(id),
    version_id INTEGER NOT NULL REFERENCES record_versions(id),
    PRIMARY KEY(run_id, record_id)
);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
    record_id INTEGER NOT NULL REFERENCES records(id),
    change_type TEXT NOT NULL,
    previous_version_id INTEGER,
    current_version_id INTEGER,
    UNIQUE(run_id, record_id)
);

CREATE INDEX IF NOT EXISTS idx_records_source
ON records(student_id, source_type, status);

CREATE INDEX IF NOT EXISTS idx_versions_record
ON record_versions(record_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_changes_run
ON changes(run_id, change_type);

CREATE TABLE IF NOT EXISTS derivations (
    id INTEGER PRIMARY KEY,
    harvest_run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
    cache_key TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'live',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    response_id TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS derived_items (
    id INTEGER PRIMARY KEY,
    derivation_id INTEGER NOT NULL REFERENCES derivations(id),
    item_index INTEGER NOT NULL,
    stable_key TEXT NOT NULL,
    stream TEXT NOT NULL,
    item_type TEXT NOT NULL,
    audience TEXT NOT NULL,
    child TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    effective_date TEXT,
    end_date TEXT,
    all_day INTEGER NOT NULL,
    timing_rule TEXT NOT NULL,
    actionability TEXT NOT NULL,
    urgency TEXT NOT NULL,
    urgency_reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    needs_review INTEGER NOT NULL,
    review_reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(derivation_id, item_index)
);

CREATE TABLE IF NOT EXISTS derivation_sources (
    derivation_id INTEGER NOT NULL REFERENCES derivations(id),
    record_id INTEGER NOT NULL REFERENCES records(id),
    version_id INTEGER NOT NULL REFERENCES record_versions(id),
    PRIMARY KEY(derivation_id, record_id, version_id)
);

CREATE TABLE IF NOT EXISTS derived_item_sources (
    item_id INTEGER NOT NULL REFERENCES derived_items(id),
    record_id INTEGER NOT NULL REFERENCES records(id),
    version_id INTEGER NOT NULL REFERENCES record_versions(id),
    PRIMARY KEY(item_id, record_id, version_id)
);

CREATE TABLE IF NOT EXISTS item_workflow (
    stable_key TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    first_delivered_at TEXT,
    last_delivered_at TEXT,
    acknowledged_at TEXT,
    completed_at TEXT,
    dismissed_at TEXT,
    archived_at TEXT,
    archive_reason TEXT,
    archive_run_id INTEGER,
    archive_reviewed_at TEXT,
    archive_review_as_of TEXT,
    archive_review_reason TEXT
);

CREATE TABLE IF NOT EXISTS archival_runs (
    id INTEGER PRIMARY KEY,
    as_of TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    candidate_count INTEGER NOT NULL,
    archived_count INTEGER NOT NULL,
    retained_count INTEGER NOT NULL,
    agent_review_count INTEGER NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS archival_decisions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES archival_runs(id),
    item_id INTEGER NOT NULL REFERENCES derived_items(id),
    stable_key TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    method TEXT NOT NULL,
    UNIQUE(run_id, stable_key)
);

CREATE INDEX IF NOT EXISTS idx_archival_decisions_run
ON archival_decisions(run_id, decision);

CREATE TABLE IF NOT EXISTS consolidations (
    id INTEGER PRIMARY KEY,
    harvest_run_id INTEGER NOT NULL REFERENCES harvest_runs(id),
    cache_key TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    input_count INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    response_id TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS consolidated_items (
    id INTEGER PRIMARY KEY,
    consolidation_id INTEGER NOT NULL REFERENCES consolidations(id),
    item_index INTEGER NOT NULL,
    stable_key TEXT NOT NULL,
    resolution TEXT NOT NULL,
    stream TEXT NOT NULL,
    item_type TEXT NOT NULL,
    audience TEXT NOT NULL,
    child TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    effective_date TEXT,
    end_date TEXT,
    all_day INTEGER NOT NULL,
    timing_rule TEXT NOT NULL,
    actionability TEXT NOT NULL,
    urgency TEXT NOT NULL,
    urgency_reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    needs_review INTEGER NOT NULL,
    review_reason TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(consolidation_id, item_index)
);

CREATE TABLE IF NOT EXISTS consolidation_members (
    consolidated_item_id INTEGER NOT NULL REFERENCES consolidated_items(id),
    derived_item_id INTEGER NOT NULL REFERENCES derived_items(id),
    PRIMARY KEY(consolidated_item_id, derived_item_id)
);

CREATE INDEX IF NOT EXISTS idx_derivations_harvest_run
ON derivations(harvest_run_id, status);

CREATE INDEX IF NOT EXISTS idx_derived_items_date
ON derived_items(effective_date, child, item_type);

CREATE INDEX IF NOT EXISTS idx_derived_item_sources_version
ON derived_item_sources(version_id, item_id);

CREATE INDEX IF NOT EXISTS idx_derivation_sources_version
ON derivation_sources(version_id, derivation_id);

CREATE INDEX IF NOT EXISTS idx_consolidations_harvest_run
ON consolidations(harvest_run_id, status);

CREATE INDEX IF NOT EXISTS idx_consolidation_members_derived
ON consolidation_members(derived_item_id, consolidated_item_id);

CREATE TABLE IF NOT EXISTS message_thread_state (
    thread_ref TEXT PRIMARY KEY,
    student_id INTEGER NOT NULL,
    child TEXT NOT NULL,
    participant_display_json TEXT NOT NULL,
    exists_now INTEGER NOT NULL,
    server_unread INTEGER NOT NULL,
    is_new_thread INTEGER NOT NULL,
    has_new_message INTEGER NOT NULL,
    latest_unretrieved INTEGER NOT NULL,
    latest_activity_at TEXT,
    inbox_position INTEGER NOT NULL,
    latest_message_ref TEXT NOT NULL,
    latest_inbox_message_ref TEXT,
    last_retrieved_message_ref TEXT,
    observed_at TEXT NOT NULL,
    policy_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_message_thread_state_student
ON message_thread_state(student_id, server_unread, has_new_message);

CREATE TABLE IF NOT EXISTS message_event_facts (
    id INTEGER PRIMARY KEY,
    stable_key TEXT NOT NULL UNIQUE,
    thread_ref TEXT NOT NULL,
    message_ref TEXT NOT NULL,
    child TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    end_date TEXT,
    all_day INTEGER NOT NULL,
    cancellation_state TEXT NOT NULL,
    necessary_location TEXT,
    bring_items_json TEXT NOT NULL DEFAULT '[]',
    requirement TEXT,
    activity_kind TEXT,
    participant_display_json TEXT NOT NULL,
    template_key TEXT NOT NULL,
    confidence REAL NOT NULL,
    needs_review INTEGER NOT NULL,
    workflow_state TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_message_event_facts_thread
ON message_event_facts(thread_ref, workflow_state, effective_date);

CREATE TABLE IF NOT EXISTS message_processing_receipts (
    thread_ref TEXT NOT NULL,
    message_ref TEXT NOT NULL,
    result_code TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY(thread_ref, message_ref)
);

CREATE TABLE IF NOT EXISTS message_processing_attempts (
    thread_ref TEXT NOT NULL,
    message_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    error_code TEXT,
    response_id TEXT,
    http_status INTEGER,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    attempted_at TEXT NOT NULL,
    PRIMARY KEY(thread_ref, message_ref)
);

CREATE TABLE IF NOT EXISTS message_event_feedback (
    id INTEGER PRIMARY KEY,
    thread_ref TEXT NOT NULL,
    stable_key TEXT,
    verdict TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_message_event_feedback_verdict
ON message_event_feedback(verdict, recorded_at);

CREATE TABLE IF NOT EXISTS day_summary_runs (
    id INTEGER PRIMARY KEY,
    cache_key TEXT NOT NULL UNIQUE,
    record_id INTEGER NOT NULL REFERENCES records(id),
    version_id INTEGER NOT NULL REFERENCES record_versions(id),
    child TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    response_id TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_day_summary_runs_record
ON day_summary_runs(record_id, content_hash, status);

CREATE TABLE IF NOT EXISTS day_summaries (
    run_id INTEGER NOT NULL REFERENCES day_summary_runs(id),
    date TEXT NOT NULL,
    headline TEXT,
    text TEXT,
    general_used INTEGER NOT NULL,
    PRIMARY KEY(run_id, date)
);

-- The review check's verdict on a fact flagged for review, for one version of it.
CREATE TABLE IF NOT EXISTS review_verdicts (
    stable_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('show', 'drop', 'unclear')),
    note TEXT,
    reason TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('rule', 'model')),
    decided_at TEXT NOT NULL,
    PRIMARY KEY(stable_key, fingerprint)
);
