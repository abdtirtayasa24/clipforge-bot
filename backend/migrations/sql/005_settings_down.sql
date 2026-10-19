DELETE FROM app_settings WHERE key IN (
    'min_score', 'clip_min_s', 'clip_max_s', 'max_clips', 'max_overlap',
    'affiliate_links_enabled', 'affiliate_min_relevance', 'affiliate_disclosure_text',
    'reserved_disk_bytes', 'permitted_download_domains', 'audio_chunk_minutes',
    'discovery', 'models'
);
DELETE FROM ai_provider_configs WHERE (provider, model, capability) IN (
    ('gemini', 'gemini-3.5-transcribe', 'transcription'),
    ('gemini', 'gemini-3.5-flash-lite', 'analysis'),
    ('gemini', 'gemini-3-flash-preview', 'rerank'),
    ('openrouter', 'nvidia/nemotron-3.5-super-vl:free', 'analysis'),
    ('gemini', 'gemini-embedding-2', 'embedding')
);

ALTER TABLE clip_reviews DROP COLUMN title_override;
ALTER TABLE clips DROP COLUMN title_override, DROP COLUMN title;
ALTER TABLE provider_usage
    DROP COLUMN currency,
    DROP COLUMN estimated_cost,
    DROP COLUMN audio_seconds,
    DROP COLUMN stage;
ALTER TABLE ai_provider_configs
    DROP COLUMN currency,
    DROP COLUMN price_per_audio_minute,
    DROP COLUMN price_output_per_mtok,
    DROP COLUMN price_input_per_mtok,
    DROP COLUMN quota_reset,
    DROP COLUMN rpd_limit,
    DROP COLUMN rpm_limit;
