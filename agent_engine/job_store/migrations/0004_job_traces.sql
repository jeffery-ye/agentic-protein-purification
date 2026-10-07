-- A job's trace (#77): each LLM call's reasoning summary, tokens, time and cost,
-- and each stage's time. Served by /trace, apart from the report. NULL for jobs
-- that ran before this, or recorded no trace.

ALTER TABLE jobs ADD COLUMN trace TEXT;
