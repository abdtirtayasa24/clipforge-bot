-- A running name resumes its own stage, as the previous stage's finished name does.
UPDATE processing_jobs
SET stage = CASE stage
    WHEN 'extracting_audio' THEN 'validated'
    WHEN 'transcribing' THEN 'audio_extracted'
    WHEN 'analyzing' THEN 'transcribed'
    WHEN 'selecting_candidates' THEN 'analyzed'
    WHEN 'rendering_clips' THEN 'candidates_selected'
    WHEN 'uploading_clips' THEN 'rendering'
    WHEN 'matching_affiliates' THEN 'uploading'
END
WHERE stage IN (
    'extracting_audio', 'transcribing', 'analyzing', 'selecting_candidates',
    'rendering_clips', 'uploading_clips', 'matching_affiliates'
);

ALTER TABLE processing_jobs
    DROP CONSTRAINT processing_jobs_stage_check,
    ADD CONSTRAINT processing_jobs_stage_check CHECK (stage IN (
        'awaiting_source', 'queued', 'ingesting', 'validated', 'audio_extracted',
        'transcribed', 'analyzed', 'candidates_selected', 'rendering', 'uploading',
        'affiliate_matching', 'completed', 'completed_no_clips', 'failed', 'cancelled'
    ));
