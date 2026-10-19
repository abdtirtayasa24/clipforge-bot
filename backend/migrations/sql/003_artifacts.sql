ALTER TABLE media_assets
    ADD COLUMN kind TEXT NOT NULL CHECK (kind IN ('upload', 'url')),
    ADD COLUMN source_url TEXT CHECK (source_url ~ '^https://'),
    -- Display only: stored files always get UUID names, never this value.
    ADD COLUMN original_filename TEXT CHECK (length(original_filename) <= 255),
    ADD COLUMN local_path TEXT,
    ADD COLUMN size_bytes BIGINT CHECK (size_bytes > 0),
    ADD COLUMN received_bytes BIGINT NOT NULL DEFAULT 0 CHECK (received_bytes >= 0),
    ADD COLUMN sha256 CHAR(64) CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN permission_evidence JSONB NOT NULL
        CHECK (jsonb_typeof(permission_evidence) = 'object'),
    ADD COLUMN state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'receiving', 'ready', 'failed', 'deleted')),
    ADD COLUMN media_info JSONB,
    ADD COLUMN failure_reason TEXT,
    ADD CONSTRAINT media_assets_url_kind CHECK ((kind = 'url') = (source_url IS NOT NULL)),
    ADD CONSTRAINT media_assets_received_within_size
        CHECK (size_bytes IS NULL OR received_bytes <= size_bytes);

CREATE TABLE clip_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id UUID NOT NULL REFERENCES clips (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'subtitles')),
    local_path TEXT,
    size_bytes BIGINT CHECK (size_bytes >= 0),
    sha256 CHAR(64) CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    md5 CHAR(32) CHECK (md5 ~ '^[0-9a-f]{32}$'),
    upload_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (upload_state IN ('pending', 'uploading', 'verified', 'failed')),
    drive_resumable_uri TEXT,
    drive_file_id TEXT,
    local_deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (clip_id, kind),
    CONSTRAINT clip_artifacts_verified_has_drive_file
        CHECK (upload_state <> 'verified' OR drive_file_id IS NOT NULL),
    -- ADR-014: the only local copy is never removed before the Drive copy is verified.
    CONSTRAINT clip_artifacts_delete_local_after_verify
        CHECK (local_deleted_at IS NULL OR upload_state = 'verified')
);

CREATE TRIGGER clip_artifacts_updated_at BEFORE UPDATE ON clip_artifacts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
