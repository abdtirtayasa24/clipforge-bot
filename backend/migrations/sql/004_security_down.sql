DROP INDEX provider_usage_job_id_idx;
DROP INDEX provider_usage_created_at_idx;
DROP INDEX media_assets_video_id_idx;
DROP INDEX video_sources_source_id_idx;
DROP INDEX videos_published_at_idx;
DROP INDEX clip_reviews_clip_id_idx;
DROP INDEX clip_candidates_job_id_idx;
DROP INDEX clips_job_id_idx;
DROP INDEX clips_video_id_idx;
DROP INDEX clips_review_status_idx;
DROP INDEX job_events_job_id_idx;
DROP INDEX processing_jobs_video_id_idx;
DROP INDEX processing_jobs_lease_expiry_idx;
DROP INDEX processing_jobs_claim_idx;

ALTER TABLE alembic_version DISABLE ROW LEVEL SECURITY;
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
        EXECUTE format('DROP POLICY clipforge_app_all ON %I', app_table);
        EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', app_table);
    END LOOP;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM clipforge_app;
REVOKE USAGE ON SCHEMA public FROM clipforge_app;
GRANT EXECUTE ON FUNCTION set_updated_at(), reject_job_event_change() TO PUBLIC;
-- The clipforge_app role is cluster-wide and may hold grants in other databases, so it is
-- kept. Supabase API-role grants are intentionally not restored.
