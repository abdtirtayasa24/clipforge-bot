# Reliability Report (T40, Milestone M6)

**Rule under test (spec, Gathering Results):** every failure scenario either *recovers* or
*fails visibly*. Failing visibly means the job ends `failed`, gets a `failed` event, and the
admin receives a Telegram "job failed" message. A job must never stall or end silently, and a
clip must never have more than one Drive file.

The suite in `backend/tests/reliability/` runs the production stage list (`default_stages`)
end to end: ingest, audio, transcribe, analyze, select, render, upload and affiliate. It uses
real ffmpeg, a real PostgreSQL and the fenced queue (ADR-005). Only the edges are faked:

- the AI providers (fake transcription and analysis, with injected failures);
- Google Drive (the in-memory `FakeDrive`, with resumable sessions and fault injection);
- the Telegram Bot API (captured requests);
- the YouTube Data API.

Any other outbound request fails the test, so no scenario touches the network.

```bash
cd backend && source .venv/bin/activate
CLIPFORGE_TEST_DATABASE_URL=postgresql://… python -m pytest tests/reliability
```

## Local results — 2026-10-21

Environment: Windows 11 development machine, Python 3.12.13, PostgreSQL (local), ffmpeg 2025-09
(gyan.dev build). **21 passed in 78 s.** The full backend suite also passes: 902 passed and
1 skipped in 6 min 23 s.

| # | Scenario | Test | Outcome |
|---|---|---|---|
| 1 | `kill -9` after each stage's work, before the stage is recorded (8 stages: `validated` … `affiliate_matching`) | `test_crash_recovery.py::test_kill_9_after_any_stage_resumes_without_duplicates[*]` | **Recovers.** The lease is held until it expires; then a second worker reclaims the job (`reclaimed` event) and finishes from the last recorded stage. Each clip is verified on Drive exactly once, and no audio chunk is transcribed twice (AI cost is not paid twice). |
| 2 | Lease expiry with two workers: worker A freezes just before starting an upload; B reclaims the job and is mid-upload when A wakes | `test_crash_recovery.py::test_stale_worker_waking_mid_upload_never_uploads_twice` | **Recovers.** A ends `lease_lost` with no writes; B completes; one Drive file per clip. *This scenario found a defect, fixed in this task (see below).* |
| 3 | DB connections dropped mid-job (`pg_terminate_backend` on every connection) | `test_crash_recovery.py::test_dropped_database_connections_are_replaced_mid_job` | **Recovers** transparently: `pool_pre_ping` replaces the dead connections. |
| 4 | DB outage while a stage, and then its error, is being recorded | `test_crash_recovery.py::test_database_outage_abandons_the_attempt_and_the_job_resumes` | **Recovers.** The attempt is abandoned without writes and the worker process exits; `Restart=always` brings it back. The job keeps its lease, is reclaimed after expiry, and resumes from the last recorded stage. |
| 5 | Provider timeout | `test_external_failures.py::test_provider_timeout_is_retried_with_backoff` | **Recovers.** `retry_scheduled` with backoff, then completes. |
| 6 | Provider 429 / quota | `test_external_failures.py::test_provider_rate_limit_defers_without_using_an_attempt` | **Recovers.** A `quota_deferred` event is recorded. The job is not claimable before the reset time and does not use up an attempt (ADR-028). |
| 7 | Provider fails on every attempt | `test_external_failures.py::test_provider_failing_every_attempt_fails_visibly` | **Fails visibly** after 3 attempts: `failed` status, `failed` event, and a Telegram message with the reason. |
| 8 | Drive HTTP 503 mid-upload | `test_external_failures.py::test_drive_5xx_mid_upload_resumes_the_same_session` | **Recovers.** The retry resumes the persisted resumable session, so no second session or file is created. |
| 9 | Drive ambiguous completion (file stored, response lost) | `test_external_failures.py::test_ambiguous_drive_completion_adopts_the_uploaded_file` | **Recovers.** The rerun finds the file by its `clipforge_clip_id` app property and adopts it after matching size and MD5 (ADR-014). |
| 10 | Disk full at download admission | `test_external_failures.py::test_full_disk_defers_downloads_then_fails_visibly` | **Fails visibly.** Each attempt is deferred ("not enough free disk"); after 3 attempts the job is `failed`, with an event and a Telegram message. Upload-time admission (HTTP 507) is covered by `tests/integration/test_uploads_api.py::test_disk_admission_rejects_or_defers`. |
| 11 | YouTube API failure: 403 `quotaExceeded`, 503, network down | `test_external_failures.py::test_discovery_api_failure_is_reported_and_exits_non_zero[*]` | **Fails visibly.** The discovery summary names the failed source, the CLI exits 1 (the systemd unit shows *failed*), the source's `last_run_at` is not advanced, and the API key never appears in the message. |
| 12 | Cancellation mid-render | `test_cancellation_mid_render.py::test_cancel_mid_render_stops_after_the_current_clip` | **Stops cleanly.** The job ends after the clip being rendered: `cancel_requested` then `cancelled` events, no Drive request, no `.part.mp4` left behind, no failure message. |

Earlier milestones already covered these failure paths at unit and integration level, so this
suite does not repeat them:

- fenced writes and lease reclaim: `tests/integration/test_queue.py`
- heartbeat and lost lease: `tests/integration/test_worker.py`
- resumable upload, checksum mismatch and expired session: `tests/integration/test_drive_upload.py`
- Gemini / OpenRouter error mapping: `tests/unit/test_gemini_transcription.py`, `tests/unit/test_analysis.py`
- notification failure never changes a job: `tests/integration/test_notify.py`
- workspace GC after a crash: `tests/integration/test_cleanup.py`

**Acceptance criteria:**

- Every scenario either recovers or fails visibly: ✅ (all 21 tests above).
- 0 duplicate Drive files: ✅ (every pipeline scenario asserts at most one live file per clip;
  every recovered job has exactly one).

## Defect found and fixed: stale worker could upload a second copy

Drive calls cannot be fenced by the database lease. In this sequence:

1. Worker A is frozen (SIGSTOP, VM pause, or a database outage long enough for the lease to
   lapse) after checking Drive for an existing file.
2. Worker B reclaims the job and begins uploading the same clip.
3. Worker A wakes.

A would then start its own upload session and write a second copy. During the test it also
deleted the local clip files out from under B.

**Fix:** `UploadStage` renews the lease, which is a fenced write, immediately before starting a
new upload session. A stale worker gets `LeaseLost` there and aborts before any Drive write.
Resuming an existing session needs no check, because both workers write into the same session
and Drive turns one session into one file.

**Residual window:** A could still freeze for more than the 5-minute lease in the milliseconds
between the renewal and the session start. With a single worker process per VPS
(`clipforge-worker.service`) this requires a pause at exactly that instruction, and its only
effect is one extra Drive copy. It cannot cause data loss.

## VPS scenario run

**Status: pending — run on the VPS by the operator.** These checks cannot run from the
development machine. Run them after deploying the T40 commit, and paste the log below.

Prerequisites: deploy/README.md sections 1–14 are done, Drive is connected, and two authorized
test videos are registered. Variables are as in deploy/README.md section 12 (`API`, `TOKEN`).

### 1. `kill -9` at every stage

Queue a job, then kill the worker each time the job reaches a new stage. The job must still end
`completed` with one Drive file per clip. Each kill costs a 5-minute lease wait, and a job has
3 attempts, so kill at most twice per job: use stages 1–2 on job A, 3–4 on job B, and so on.

```bash
kill_at() {  # kill_at <job id> <stage>...
  for stage in "$@"; do
    until [ "$(curl -s "$API/jobs/$1" -H "Authorization: Bearer $TOKEN" | jq -r .stage)" = "$stage" ]; do sleep 2; done
    echo "$(date +%T) job $1 reached $stage -> SIGKILL"
    sudo systemctl kill -s SIGKILL clipforge-worker
  done
}
kill_at <job A> ingesting audio_extracted 2>&1 | tee -a ~/t40-vps.log
```

Use `transcribed analyzed` for job B, `candidates_selected rendering` for job C, and
`uploading` for job D. Then check every job and its clips:

```sql
SELECT id, status, stage, attempts, last_error FROM processing_jobs WHERE id IN (...);
SELECT event_type, stage, created_at FROM job_events WHERE job_id = '<job id>' ORDER BY id;
SELECT c.job_id, c.id, c.drive_file_id, a.upload_state
FROM clips c JOIN clip_artifacts a ON a.clip_id = c.id AND a.kind = 'video'
WHERE c.job_id IN (...);
```

Expected for each job: `completed` (or `failed` with a Telegram message if it ran out of
attempts), a `reclaimed` event after each kill, and in the Drive **ClipForge Bot** folder one
file per clip id.

### 2. Database unreachable

```bash
sudo iptables -I OUTPUT -p tcp --dport 5432 -j REJECT   # while a job is in `rendering`
sleep 120; sudo iptables -D OUTPUT -p tcp --dport 5432 -j REJECT
journalctl -u clipforge-worker --since "-10 min" | tail -50
```

Use port 6543 instead of 5432 if `CLIPFORGE_DATABASE_URL` points at the Supavisor pooler.
Expected: "lease renewal failed" warnings, possibly a worker restart, then the job completes
after its lease expires.

### 3. Disk pressure

```bash
sudo fallocate -l <size> /var/lib/clipforge-bot/fill.tmp   # leave < 5 GB free
```

Register a permitted-URL source and queue its job: it should defer with "not enough free disk"
and finally fail with a Telegram message. Afterwards run `sudo rm /var/lib/clipforge-bot/fill.tmp`.

### 4. Discovery with a bad key

```bash
sudo cp -p /etc/clipforge-bot/backend.env /etc/clipforge-bot/backend.env.bak  # keeps 0640
sudo sed -i 's/^YOUTUBE_API_KEY=.*/YOUTUBE_API_KEY=invalid/' /etc/clipforge-bot/backend.env
sudo systemd-run --wait --pipe --uid=clipforge --gid=clipforge \n  -p EnvironmentFile=/etc/clipforge-bot/backend.env -p WorkingDirectory=/opt/clipforge-bot/backend \n  /opt/clipforge-bot/backend/.venv/bin/python -m clipforge.discovery_cli
sudo mv /etc/clipforge-bot/backend.env.bak /etc/clipforge-bot/backend.env
```

Expected: the unit ends *failed* (exit code 1) and the admin chat receives a discovery summary
naming the failed source.

### Log

```text
(paste ~/t40-vps.log, the SQL results and the relevant journalctl lines here)
```
