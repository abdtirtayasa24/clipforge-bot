CREATE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END
$$;

CREATE TABLE admin_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id BIGINT NOT NULL UNIQUE CHECK (telegram_user_id > 0),
    display_name TEXT,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE youtube_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('trending', 'channel')),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    channel_id TEXT,
    region_code VARCHAR(2) CHECK (region_code ~ '^[A-Z]{2}$'),
    enabled BOOLEAN NOT NULL DEFAULT true,
    last_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((kind = 'channel') = (channel_id IS NOT NULL)),
    UNIQUE (channel_id)
);

CREATE TABLE videos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    youtube_video_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    channel_id TEXT NOT NULL,
    channel_title TEXT,
    category_id TEXT,
    duration_seconds INTEGER CHECK (duration_seconds >= 0),
    published_at TIMESTAMPTZ,
    view_count BIGINT CHECK (view_count >= 0),
    like_count BIGINT CHECK (like_count >= 0),
    comment_count BIGINT CHECK (comment_count >= 0),
    default_language TEXT,
    thumbnail_url TEXT,
    eligibility TEXT NOT NULL DEFAULT 'pending'
        CHECK (eligibility IN ('pending', 'eligible', 'ineligible')),
    ineligibility_reason TEXT,
    rights_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (rights_status IN ('unverified', 'authorized', 'rejected')),
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE video_sources (
    video_id UUID NOT NULL REFERENCES videos (id) ON DELETE CASCADE,
    source_id UUID NOT NULL REFERENCES youtube_sources (id) ON DELETE CASCADE,
    category_id TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (video_id, source_id)
);

CREATE TABLE media_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID NOT NULL REFERENCES videos (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE processing_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID NOT NULL REFERENCES videos (id) ON DELETE RESTRICT,
    media_asset_id UUID REFERENCES media_assets (id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clip_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES processing_jobs (id) ON DELETE CASCADE,
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms INTEGER NOT NULL,
    hook_score SMALLINT NOT NULL CHECK (hook_score BETWEEN 0 AND 100),
    emotion_score SMALLINT NOT NULL CHECK (emotion_score BETWEEN 0 AND 100),
    clarity_score SMALLINT NOT NULL CHECK (clarity_score BETWEEN 0 AND 100),
    relevance_score SMALLINT NOT NULL CHECK (relevance_score BETWEEN 0 AND 100),
    visual_score SMALLINT NOT NULL CHECK (visual_score BETWEEN 0 AND 100),
    completeness_score SMALLINT NOT NULL CHECK (completeness_score BETWEEN 0 AND 100),
    total_score NUMERIC(5, 2) NOT NULL CHECK (total_score BETWEEN 0 AND 100),
    title TEXT CHECK (length(title) <= 70),
    hook_line TEXT,
    explanation TEXT,
    transcript_excerpt TEXT,
    selected BOOLEAN NOT NULL DEFAULT false,
    rank SMALLINT CHECK (rank >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_ms > start_ms)
);

CREATE TABLE clips (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id UUID NOT NULL UNIQUE REFERENCES clip_candidates (id) ON DELETE RESTRICT,
    job_id UUID NOT NULL REFERENCES processing_jobs (id) ON DELETE RESTRICT,
    video_id UUID NOT NULL REFERENCES videos (id) ON DELETE RESTRICT,
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    end_ms INTEGER NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (review_status IN ('pending_review', 'approved', 'rejected')),
    drive_file_id TEXT,
    drive_web_view_link TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_ms > start_ms)
);

CREATE TABLE clip_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id UUID NOT NULL REFERENCES clips (id) ON DELETE CASCADE,
    admin_user_id UUID NOT NULL REFERENCES admin_users (id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ai_provider_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL CHECK (provider IN ('gemini', 'openrouter')),
    model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
    capability TEXT NOT NULL
        CHECK (capability IN ('transcription', 'analysis', 'rerank', 'embedding')),
    -- Name of the environment variable holding the key; never the key itself.
    secret_ref TEXT NOT NULL CHECK (secret_ref ~ '^[A-Z][A-Z0-9_]*$'),
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, model, capability)
);

CREATE TABLE provider_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES processing_jobs (id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    capability TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    error_code TEXT,
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    input_tokens INTEGER CHECK (input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE app_settings (
    key TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    value JSONB NOT NULL,
    updated_by UUID REFERENCES admin_users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE oauth_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL UNIQUE CHECK (provider IN ('google_drive')),
    encrypted_refresh_token TEXT NOT NULL,
    scopes TEXT[] NOT NULL DEFAULT '{}',
    account_email TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER admin_users_updated_at BEFORE UPDATE ON admin_users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER youtube_sources_updated_at BEFORE UPDATE ON youtube_sources
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER videos_updated_at BEFORE UPDATE ON videos
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER media_assets_updated_at BEFORE UPDATE ON media_assets
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER processing_jobs_updated_at BEFORE UPDATE ON processing_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER clips_updated_at BEFORE UPDATE ON clips
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER ai_provider_configs_updated_at BEFORE UPDATE ON ai_provider_configs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER app_settings_updated_at BEFORE UPDATE ON app_settings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER oauth_credentials_updated_at BEFORE UPDATE ON oauth_credentials
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
