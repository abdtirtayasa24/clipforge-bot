-- Runtime role (ADR-003). It is a NOLOGIN group: each environment creates its own login
-- user out-of-band, e.g. CREATE ROLE clipforge_app_prod LOGIN PASSWORD '...' IN ROLE clipforge_app;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clipforge_app') THEN
        CREATE ROLE clipforge_app NOLOGIN;
    END IF;
END
$$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO clipforge_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    admin_users, youtube_sources, videos, video_sources, media_assets, processing_jobs,
    clip_candidates, clips, clip_artifacts, ai_provider_configs, provider_usage,
    app_settings, oauth_credentials
TO clipforge_app;
-- Audit trails are append-only for the runtime role.
GRANT SELECT, INSERT ON job_events, clip_reviews TO clipforge_app;

REVOKE ALL ON FUNCTION set_updated_at(), reject_job_event_change() FROM PUBLIC;

-- The app never uses Supabase's Data API: strip the grants Supabase gives its API roles
-- on everything in `public`, including defaults for objects the migration role creates later.
DO $$
DECLARE
    api_role TEXT;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', api_role);
            EXECUTE format('REVOKE CREATE ON SCHEMA public FROM %I', api_role);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', api_role);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I',
                api_role);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I',
                api_role);
        END IF;
    END LOOP;
END
$$;

-- RLS on every table; only clipforge_app has policies. alembic_version gets none, so only
-- its owner (the migration role, which bypasses RLS as owner) can read it.
DO $$
DECLARE
    app_table TEXT;
BEGIN
    FOREACH app_table IN ARRAY ARRAY[
        'admin_users', 'youtube_sources', 'videos', 'video_sources', 'media_assets',
        'processing_jobs', 'job_events', 'clip_candidates', 'clips', 'clip_artifacts',
        'clip_reviews', 'ai_provider_configs', 'provider_usage', 'app_settings',
        'oauth_credentials'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', app_table);
        EXECUTE format(
            'CREATE POLICY clipforge_app_all ON %I TO clipforge_app USING (true) WITH CHECK (true)',
            app_table);
    END LOOP;
END
$$;
ALTER TABLE alembic_version ENABLE ROW LEVEL SECURITY;

CREATE INDEX processing_jobs_claim_idx ON processing_jobs (status, next_attempt_at);
CREATE INDEX processing_jobs_lease_expiry_idx ON processing_jobs (lease_expires_at)
    WHERE status = 'running';
CREATE INDEX processing_jobs_video_id_idx ON processing_jobs (video_id);
CREATE INDEX job_events_job_id_idx ON job_events (job_id, id);
CREATE INDEX clips_review_status_idx ON clips (review_status, created_at DESC);
CREATE INDEX clips_video_id_idx ON clips (video_id);
CREATE INDEX clips_job_id_idx ON clips (job_id);
CREATE INDEX clip_candidates_job_id_idx ON clip_candidates (job_id);
CREATE INDEX clip_reviews_clip_id_idx ON clip_reviews (clip_id, created_at);
CREATE INDEX videos_published_at_idx ON videos (published_at DESC);
CREATE INDEX video_sources_source_id_idx ON video_sources (source_id);
CREATE INDEX media_assets_video_id_idx ON media_assets (video_id);
CREATE INDEX provider_usage_created_at_idx ON provider_usage (created_at);
CREATE INDEX provider_usage_job_id_idx ON provider_usage (job_id);
