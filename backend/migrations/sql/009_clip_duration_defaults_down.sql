UPDATE app_settings
SET value = CASE key WHEN 'clip_min_s' THEN '20'::jsonb ELSE '60'::jsonb END,
    updated_at = now()
WHERE key IN ('clip_min_s', 'clip_max_s')
    AND (SELECT value FROM app_settings WHERE key = 'clip_min_s') = '60'::jsonb
    AND (SELECT value FROM app_settings WHERE key = 'clip_max_s') = '90'::jsonb;
