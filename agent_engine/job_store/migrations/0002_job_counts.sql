-- New jobs per UTC day, for the daily job cap (#26). Kept apart from jobs, so
-- deleting jobs doesn't free up the day's allowance.

CREATE TABLE job_counts (
    day TEXT PRIMARY KEY,
    created INTEGER NOT NULL
);

INSERT INTO job_counts (day, created)
SELECT substr(created_at, 1, 10), COUNT(*) FROM jobs GROUP BY substr(created_at, 1, 10);
