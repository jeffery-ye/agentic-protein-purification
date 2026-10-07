-- Jobs and their progress history (#24).

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    -- Filled by sign-in (#25); NULL until then.
    owner_sub TEXT,
    owner_username TEXT,
    -- The PurificationRequest as JSON.
    inputs TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (state IN ('queued', 'running', 'completed', 'failed', 'interrupted')),
    -- The latest status_callback message.
    progress TEXT,
    -- The ProtocolResult as JSON. Not migrated when its shape changes.
    result TEXT,
    error TEXT,
    model TEXT,
    app_version TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX jobs_by_owner ON jobs (owner_sub, created_at);

CREATE TABLE job_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX job_progress_by_job ON job_progress (job_id, id);
