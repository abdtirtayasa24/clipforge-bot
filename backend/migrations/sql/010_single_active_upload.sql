-- One upload receives bytes at a time (ADR-032). Older uploads still receiving when this runs
-- are failed so the index can be built; only the newest one stays resumable.
UPDATE media_assets
SET state = 'failed', failure_reason = 'superseded: one upload at a time'
WHERE kind = 'upload'
    AND state = 'receiving'
    AND id <> (
        SELECT id FROM media_assets
        WHERE kind = 'upload' AND state = 'receiving'
        ORDER BY created_at DESC, id
        LIMIT 1
    );

CREATE UNIQUE INDEX media_assets_one_receiving_upload ON media_assets ((true))
    WHERE kind = 'upload' AND state = 'receiving';
