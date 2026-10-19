DROP TABLE clip_artifacts;

ALTER TABLE media_assets
    DROP CONSTRAINT media_assets_received_within_size,
    DROP CONSTRAINT media_assets_url_kind,
    DROP COLUMN failure_reason,
    DROP COLUMN media_info,
    DROP COLUMN state,
    DROP COLUMN permission_evidence,
    DROP COLUMN sha256,
    DROP COLUMN received_bytes,
    DROP COLUMN size_bytes,
    DROP COLUMN local_path,
    DROP COLUMN original_filename,
    DROP COLUMN source_url,
    DROP COLUMN kind;
