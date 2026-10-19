ALTER TABLE ai_provider_configs
    ADD COLUMN rpm_limit INTEGER CHECK (rpm_limit > 0),
    ADD COLUMN rpd_limit INTEGER CHECK (rpd_limit > 0),
    ADD COLUMN quota_reset TEXT NOT NULL DEFAULT 'utc_midnight'
        CHECK (quota_reset IN ('pacific_midnight', 'utc_midnight')),
    ADD COLUMN price_input_per_mtok NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (price_input_per_mtok >= 0),
    ADD COLUMN price_output_per_mtok NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (price_output_per_mtok >= 0),
    ADD COLUMN price_per_audio_minute NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (price_per_audio_minute >= 0),
    ADD COLUMN currency CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$');

ALTER TABLE provider_usage
    ADD COLUMN stage TEXT,
    ADD COLUMN audio_seconds NUMERIC(10, 3) CHECK (audio_seconds >= 0),
    ADD COLUMN estimated_cost NUMERIC(14, 8) NOT NULL DEFAULT 0 CHECK (estimated_cost >= 0),
    ADD COLUMN currency CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$');

ALTER TABLE clips
    ADD COLUMN title TEXT CHECK (length(title) <= 70),
    ADD COLUMN title_override TEXT CHECK (length(title_override) BETWEEN 1 AND 100);

ALTER TABLE clip_reviews
    ADD COLUMN title_override TEXT CHECK (length(title_override) BETWEEN 1 AND 100);

-- Free-tier quotas are not published as fixed numbers (ADR-027, ADR-028): these limits are
-- conservative placeholders to be replaced with the project's AI Studio values. Free tier
-- models cost 0.
INSERT INTO ai_provider_configs
    (provider, model, capability, secret_ref, rpm_limit, rpd_limit, quota_reset)
VALUES
    ('gemini', 'gemini-3.5-transcribe', 'transcription', 'GEMINI_API_KEY', 5, 100,
        'pacific_midnight'),
    ('gemini', 'gemini-3.5-flash-lite', 'analysis', 'GEMINI_API_KEY', 10, 200,
        'pacific_midnight'),
    ('gemini', 'gemini-3-flash-preview', 'rerank', 'GEMINI_API_KEY', 5, 50,
        'pacific_midnight'),
    ('openrouter', 'nvidia/nemotron-3.5-super-vl:free', 'analysis', 'OPENROUTER_API_KEY', 20, 50,
        'utc_midnight'),
    ('gemini', 'gemini-embedding-2', 'embedding', 'GEMINI_API_KEY', 10, 200,
        'pacific_midnight');

INSERT INTO app_settings (key, value) VALUES
    ('min_score', '75'),
    ('clip_min_s', '20'),
    ('clip_max_s', '60'),
    ('max_clips', '3'),
    ('max_overlap', '0.2'),
    ('affiliate_links_enabled', 'false'),
    ('affiliate_min_relevance', '0.70'),
    ('affiliate_disclosure_text',
        '"Affiliate link — I may earn a commission from qualifying purchases."'),
    ('reserved_disk_bytes', '5368709120'),
    ('permitted_download_domains', '[]'),
    ('audio_chunk_minutes', '20'),
    ('discovery', '{
        "region_code": "ID",
        "top_categories": 5,
        "chart_pages": 4,
        "per_category_results": 50,
        "max_new_eligible_per_run": 20,
        "excluded_category_ids": []
    }'),
    ('models', '{
        "transcription": "gemini-3.5-transcribe",
        "analysis": "gemini-3.5-flash-lite",
        "rerank": "gemini-3-flash-preview",
        "analysis_fallback": "openrouter:nvidia/nemotron-3.5-super-vl:free",
        "embedding": "gemini-embedding-2"
    }');
