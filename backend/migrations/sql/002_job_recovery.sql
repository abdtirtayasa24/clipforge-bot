ALTER TABLE processing_jobs
    ADD COLUMN idempotency_key TEXT NOT NULL
        CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN (
            'awaiting_source', 'queued', 'running', 'retry_scheduled',
            'completed', 'completed_no_clips', 'failed', 'cancelled'
        )),
    ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued'
        CHECK (stage IN (
            'awaiting_source', 'queued', 'ingesting', 'validated', 'audio_extracted',
            'transcribed', 'analyzed', 'candidates_selected', 'rendering', 'uploading',
            'affiliate_matching', 'completed', 'completed_no_clips', 'failed', 'cancelled'
        )),
    ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    ADD COLUMN next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN lease_token UUID,
    ADD COLUMN lease_version BIGINT NOT NULL DEFAULT 0 CHECK (lease_version >= 0),
    ADD COLUMN lease_expires_at TIMESTAMPTZ,
    ADD COLUMN leased_by TEXT,
    ADD COLUMN settings_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN last_error TEXT,
    ADD COLUMN started_at TIMESTAMPTZ,
    ADD COLUMN finished_at TIMESTAMPTZ,
    ADD CONSTRAINT processing_jobs_idempotency_key_key UNIQUE (idempotency_key),
    ADD CONSTRAINT processing_jobs_lease_consistent
        CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL));

CREATE UNIQUE INDEX processing_jobs_one_active_per_video ON processing_jobs (video_id)
    WHERE status IN ('awaiting_source', 'queued', 'running', 'retry_scheduled');

CREATE TABLE job_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES processing_jobs (id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (event_type ~ '^[a-z][a-z_]*$'),
    stage TEXT,
    status TEXT,
    attempt INTEGER CHECK (attempt >= 0),
    lease_version BIGINT,
    message TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE FUNCTION reject_job_event_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'job_events is append-only';
END
$$;

CREATE TRIGGER job_events_append_only BEFORE UPDATE OR DELETE ON job_events
    FOR EACH ROW EXECUTE FUNCTION reject_job_event_change();
CREATE TRIGGER job_events_no_truncate BEFORE TRUNCATE ON job_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_job_event_change();
