-- Every pipeline stage records a running name while it works, so a job's stage shows the step
-- that is actually running (or that failed) rather than the last one that finished.
ALTER TABLE processing_jobs
    DROP CONSTRAINT processing_jobs_stage_check,
    ADD CONSTRAINT processing_jobs_stage_check CHECK (stage IN (
        'awaiting_source', 'queued', 'ingesting', 'validated', 'extracting_audio',
        'audio_extracted', 'transcribing', 'transcribed', 'analyzing', 'analyzed',
        'selecting_candidates', 'candidates_selected', 'rendering_clips', 'rendering',
        'uploading_clips', 'uploading', 'matching_affiliates', 'affiliate_matching',
        'completed', 'completed_no_clips', 'failed', 'cancelled'
    ));
