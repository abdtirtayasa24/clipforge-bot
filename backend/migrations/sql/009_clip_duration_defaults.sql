-- New default clip duration of 60–90 s; a range the administrator already changed is kept.
UPDATE app_settings
SET value = CASE key WHEN 'clip_min_s' THEN '60'::jsonb ELSE '90'::jsonb END,
    updated_at = now()
WHERE key IN ('clip_min_s', 'clip_max_s')
    AND (SELECT value FROM app_settings WHERE key = 'clip_min_s') = '20'::jsonb
    AND (SELECT value FROM app_settings WHERE key = 'clip_max_s') = '60'::jsonb;
