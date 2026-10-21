# Production Rollout and 7-Day Pilot (T44, Milestone M7)

**Status: pending — runs on the VPS for 7 days, started by the operator.**

## 1. Release

Do these steps on the development machine, after T40–T43 are merged and CI is green:

```bash
git tag -a v1.0.0 -m "ClipForge Bot 1.0.0"
git push origin main v1.0.0
```

Then deploy on the VPS:

```bash
cd ~/clipforge-bot && git fetch --tags && git checkout v1.0.0
sudo ./deploy/install.sh && ./deploy/deploy_frontend.sh
sudo cp deploy/nginx/clipforge-bot.conf /etc/nginx/sites-available/clipforge-bot.conf
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl enable --now clipforge-api clipforge-worker clipforge-discovery.timer
curl -s 127.0.0.1:8000/api/v1/health/ready
```

The Nginx site changed in T41 (security headers), hence the copy. Before day 1, finish the
pending VPS items:

- the reliability run (`docs/reliability-report.md`);
- the manual security checks (`docs/security-checklist.md`);
- the benchmark, which also sets `daily_job_cap` (`docs/benchmark-report.md`);
- the nightly backup cron job (`docs/backup-restore.md`).

## 2. Pilot routine (daily, about 10 minutes)

- Queue representative authorized videos up to the daily cap, and review every new clip in
  the Mini App (approve if it is usable as published, reject otherwise).
- On at least two days, turn **Affiliate links** off for part of the day. Queue a job while it
  is off, then turn it back on.
- On each day with affiliate links on, approve at least one link and copy one caption. Check
  that it contains only approved links and the disclosure text.
- Run the queries below and fill in the day's row.

## 3. Daily metrics

Run all queries in the Supabase SQL editor. Days are UTC; replace `<start>` with the first pilot
day (`YYYY-MM-DD`).

```sql
WITH days AS (
  SELECT generate_series(date '<start>', date '<start>' + 6, interval '1 day')::date AS day
), jobs AS (
  SELECT finished_at::date AS day, status FROM processing_jobs
  WHERE finished_at >= date '<start>' AND status <> 'cancelled'
), pilot_clips AS (
  SELECT c.created_at::date AS day, c.job_id, c.end_ms - c.start_ms AS ms, p.status AS job_status,
         c.drive_file_id, a.upload_state
  FROM clips c
  JOIN processing_jobs p ON p.id = c.job_id
  LEFT JOIN clip_artifacts a ON a.clip_id = c.id AND a.kind = 'video'
  WHERE c.created_at >= date '<start>'
)
SELECT d.day,
  (SELECT count(*) FROM jobs j WHERE j.day = d.day) AS jobs_finished,
  (SELECT round(100.0 * count(*) FILTER (WHERE status IN ('completed', 'completed_no_clips'))
          / nullif(count(*), 0), 1) FROM jobs j WHERE j.day = d.day) AS success_pct,
  (SELECT count(*) FROM pilot_clips c WHERE c.day = d.day) AS clips,
  (SELECT round(100.0 * count(*) FILTER (WHERE ms BETWEEN 20000 AND 60000)
          / nullif(count(*), 0), 1) FROM pilot_clips c WHERE c.day = d.day) AS duration_ok_pct,
  (SELECT max(n) FROM (SELECT count(*) AS n FROM pilot_clips c WHERE c.day = d.day
                       GROUP BY c.job_id) per_job) AS max_clips_per_source,
  (SELECT round(100.0 * count(*) FILTER (WHERE upload_state = 'verified' AND drive_file_id IS NOT NULL)
          / nullif(count(*), 0), 1)
     FROM pilot_clips c WHERE c.day = d.day AND c.job_status = 'completed') AS drive_verified_pct,
  (SELECT count(*) FROM affiliate_events e
     WHERE e.event_type = 'suggested' AND e.created_at::date = d.day) AS affiliate_suggestions
FROM days d ORDER BY d.day;
```

`duration_ok_pct` uses the default 20–60 s bounds. Use your configured `clip_min_s` and
`clip_max_s` if you changed them.

Affiliate toggle history, used to check that no suggestion was created while it was off:

```sql
SELECT created_at, new_value FROM app_setting_events
WHERE setting_key = 'affiliate_links_enabled' AND created_at >= date '<start>' ORDER BY created_at;
SELECT e.created_at, e.clip_id FROM affiliate_events e
WHERE e.event_type = 'suggested' AND e.created_at >= date '<start>' ORDER BY e.created_at;
```

Every suggestion must fall inside a period that starts with an `on` (`true`) event. Any
suggestion inside an `off` period is a failure.

Clip usability over the pilot:

```sql
SELECT count(*) FILTER (WHERE review_status = 'approved') AS approved,
       count(*) FILTER (WHERE review_status = 'rejected') AS rejected,
       round(100.0 * count(*) FILTER (WHERE review_status = 'approved')
             / nullif(count(*) FILTER (WHERE review_status <> 'pending_review'), 0), 1) AS usable_pct
FROM clips WHERE created_at >= date '<start>';
```

Duplicate Drive uploads: the number of MP4 files in the Drive folder **ClipForge Bot** must
equal this count. Each file name ends with the first 8 characters of its clip id, which helps
find any extra file.

```sql
SELECT count(*) FROM clip_artifacts WHERE kind = 'video' AND upload_state = 'verified';
```

On the VPS:

```bash
journalctl -u clipforge-discovery --since "<start>" -o cat | grep '"discovery finished"' | wc -l   # 7
journalctl -u clipforge-discovery --since "<start>" -o cat | grep -c '"failed_sources": \[\]'      # 7
journalctl -u clipforge-api --since "<start>" -o cat | grep '"admin authenticated"' \
  | grep -o '"admin_user_id": "[^"]*"' | sort | uniq -c       # only your admin id(s)
journalctl -u clipforge-api --since "<start>" -o cat | grep -c '"telegram auth rejected"'  # refused attempts
```

Lowest free disk: run a sampler from day 1 (`docs/benchmark-report.md` step 1, or a 10-minute
cron job appending `df -B1 --output=avail /var/lib/clipforge-bot | tail -1`). Take the minimum
per day.

## 4. Targets (spec, Gathering Results)

| Metric | Target |
|---|---|
| Scheduled discovery | 7/7 runs |
| Eligible processing success | ≥ 90 % |
| Duplicate Drive uploads | 0 |
| Unauthorized admin access | 0 successful attempts |
| Clip duration compliance | 100 % |
| Maximum clips per source | ≤ 3 |
| Reserved free disk | ≥ 5 GB |
| Crash recovery | Recover or fail visibly |
| Clip usability | ≥ 70 % rated usable |
| Verified Drive metadata | 100 % of completed clips |
| Affiliate toggle off | 0 link-bearing exports or new suggestions |
| Affiliate link approvals | 100 % of exported links explicitly approved |
| Affiliate disclosure | 100 % of exported affiliate captions include disclosure |

## 5. Daily log

| Day | Date | Discovery ok | Jobs | Success % | Clips | Duration ok % | Max clips/source | Drive verified % | Min free disk | Affiliate off → suggestions | Copied captions ok | Incidents |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | | | | |
| 2 | | | | | | | | | | | | |
| 3 | | | | | | | | | | | | |
| 4 | | | | | | | | | | | | |
| 5 | | | | | | | | | | | | |
| 6 | | | | | | | | | | | | |
| 7 | | | | | | | | | | | | |

## 6. Result

| Metric | Result | Met? | Deviation and fix |
|---|---|---|---|
| *(fill in after day 7, one row per target in section 4)* | | | |

**Recalibration:** after the pilot, note which score dimensions best predicted the clips you
approved. They inform the `min_score` setting and the analysis prompt (spec: recalibrate viral
ranking with real publishing data).
