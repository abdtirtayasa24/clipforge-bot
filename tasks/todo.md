# ClipForge Bot — Task List

Plan: `tasks/plan.md` · Decisions: `docs/adr/` (`ADR-NNN`) · Rules: `AGENTS.md` · Spec: `docs/SPEC-001-ClipForge-Bot.md`.

**"standard"** in a task's Verification = the backend/frontend checks in `AGENTS.md` → *Definition of done for every change*. Environment setup, `rtk` and graphify-first navigation are also in `AGENTS.md`.
Sizes: XS (1 file) · S (1–2) · M (3–5) · L (5–8, split if possible).

---

## Phase 1 — Foundation (M1)

### - [x] T01: Backend uv project scaffold
**Description:** Create `backend/` with `pyproject.toml` for package `clipforge` (Python 3.12, setuptools/hatchling build backend). Declare runtime deps (fastapi, uvicorn, pydantic-settings, sqlalchemy, psycopg[binary], alembic, httpx) and a `dev` extra (pytest, pytest-asyncio, respx, ruff, mypy), each exact-pinned (`==`) to the current stable version confirmed from official docs/PyPI. Then `uv venv --python 3.12` and `uv pip install -e ".[dev]"`. Add `clipforge/config.py` (env-driven settings, `CLIPFORGE_` prefix), JSON logging with secret-redaction filter, `clipforge/main.py` with `/api/v1/health/live`.
**Acceptance criteria:**
- [x] Fresh clone: `uv venv --python 3.12` → `uv pip install -e ".[dev]"` gives a working env on Windows and Linux; no `requirements*.txt` or `uv.lock` in the repo
- [x] Every direct dependency in `pyproject.toml` uses an exact `==` pin
- [x] `uvicorn clipforge.main:app` (venv active) serves `GET /api/v1/health/live` → `200 {"status":"ok"}`
- [x] ruff + mypy (strict on `clipforge/`) + pytest configured in `pyproject.toml`
- [x] *(Should-Have: logs)* Every log line is one JSON object with `ts`, `level`, `logger`, `msg` and, when set via `contextvars`, `request_id` / `job_id` / `stage` / `attempt`; redaction filter masks `token`, `secret`, `initData`, `Authorization`, URL query strings (unit-tested)
**Verification:** standard backend checks; `rtk curl http://127.0.0.1:8000/api/v1/health/live`
**Dependencies:** None
**Files:** `backend/pyproject.toml`, `backend/clipforge/{__init__,main,config,logging}.py`, `backend/tests/unit/test_health.py`
**Size:** M

### - [x] T02: DB layer, Alembic-runs-SQL, `001_initial.sql`
**Description:** Async SQLAlchemy engine/session factory (psycopg 3, TLS options, pool sizing for 1 API + 1 worker). Alembic configured with `migrations/sql/NNN_*.sql` executed by thin revisions (ADR-002), using a separate `CLIPFORGE_MIGRATION_DATABASE_URL`. `001_initial.sql`: `admin_users`, `youtube_sources`, `videos`, `video_sources`, `media_assets`, `processing_jobs` (core cols), `clip_candidates`, `clips`, `clip_reviews`, `ai_provider_configs`, `provider_usage`, `app_settings`, `oauth_credentials` — UUID PKs (`gen_random_uuid()`), FKs, CHECK constraints for status enums, `created_at/updated_at`. Matching down script.
**Acceptance criteria:**
- [x] `alembic upgrade head` then `downgrade base` succeed on an empty Postgres
- [x] `videos.youtube_video_id` UNIQUE; `video_sources` PK `(video_id, source_id)`
- [x] Integration test fixture creates/destroys a test DB per session from `CLIPFORGE_TEST_DATABASE_URL`
**Verification:** standard + `rtk test python -m pytest tests/integration/test_migrations.py`
**Dependencies:** T01
**Files:** `backend/alembic.ini`, `backend/migrations/{env.py,versions/0001_initial.py,sql/001_initial.sql,sql/001_initial_down.sql}`, `backend/clipforge/db/session.py`, `backend/tests/conftest.py`
**Size:** M

### - [ ] T03: `002_job_recovery.sql`, `003_artifacts.sql`, ORM models
**Description:** 002 adds to `processing_jobs`: `stage`, `status`, `attempts`, `max_attempts`, `next_attempt_at`, `lease_token`, `lease_version`, `lease_expires_at`, `idempotency_key` (UNIQUE), `settings_snapshot JSONB`, `cancel_requested`, `last_error`; partial unique index preventing >1 active job per video; `job_events` (BIGINT identity, immutable — trigger rejecting UPDATE/DELETE). 003 adds artifact lifecycle: `media_assets` (kind upload/url, path, size, sha256, permission_evidence JSONB, state), `clip_artifacts` (kind, local_path, size, sha256, upload_state, drive_resumable_uri, drive_file_id). Write SQLAlchemy 2.0 typed models for all tables + a schema-drift test comparing ORM metadata to the migrated DB.
**Acceptance criteria:**
- [ ] Inserting a second active job for the same video fails; duplicate idempotency key fails
- [ ] `UPDATE job_events` raises
- [ ] Schema-drift test passes (ORM ⇔ migrated schema)
**Verification:** standard + `rtk test python -m pytest tests/integration -k "migrations or drift"`
**Dependencies:** T02
**Files:** `backend/migrations/sql/{002,003}_*.sql` (+down), `backend/migrations/versions/{0002,0003}_*.py`, `backend/clipforge/db/models.py`, `backend/tests/integration/test_schema_drift.py`
**Size:** M

### - [ ] T04: `004_security.sql`, `005_settings.sql`
**Description:** 004: create least-privilege runtime role `clipforge_app` (NOLOGIN group role; login user created per environment out-of-band), grant DML only on app tables, no DDL; enable RLS on all tables with policies only for `clipforge_app`; revoke all from Supabase `anon`/`authenticated`; indexes for hot queries (job claim: `(status, next_attempt_at)`, clips by review status, videos by `published_at`). 005: seed `app_settings` defaults (`min_score=75`, `clip_min_s=20`, `clip_max_s=60`, `max_clips=3`, `max_overlap=0.2`, `affiliate_links_enabled=false`, `affiliate_min_relevance=0.70`, disclosure text, reserved disk 5 GB, discovery config (`region_code='ID'`, `top_categories=5`, `chart_pages=4`, `per_category_results=50`, `max_new_eligible_per_run=20`, `excluded_category_ids=[]`), `permitted_download_domains=[]`, `models.transcription='gemini-3.5-transcribe'` / `models.analysis='gemini-3.5-flash-lite'` / `models.rerank='gemini-3-flash-preview'` / `models.analysis_fallback='openrouter:nvidia/nemotron-3.5-super-vl:free'` / `models.embedding='gemini-embedding-2'`, `audio_chunk_minutes=20`) and `ai_provider_configs` rows for those models (no secrets; `secret_ref` = `GEMINI_API_KEY` / `OPENROUTER_API_KEY`; `rpm_limit`, `rpd_limit`, `quota_reset` (`pacific_midnight`/`utc_midnight`) as admin-editable columns, initially conservative placeholders to be replaced with AI Studio values) including pricing columns (0 for free tier) (`price_input_per_mtok`, `price_output_per_mtok`, `price_per_audio_minute`, `currency`, `enabled`) — ADR-024. `provider_usage` gets `stage`, `audio_seconds`, `estimated_cost`, `currency`; `clips` gets `title`, `title_override` (ADR-025).
**Acceptance criteria:**
- [ ] As `clipforge_app`: SELECT/INSERT/UPDATE work; `CREATE TABLE`/`ALTER` fail
- [ ] As `anon`: any SELECT on app tables fails
- [ ] `app_settings.affiliate_links_enabled` defaults to JSON `false`
- [ ] CHECK constraints: prices ≥ 0, `estimated_cost` ≥ 0
**Verification:** standard + `rtk test python -m pytest tests/integration/test_security_roles.py`
**Dependencies:** T03
**Files:** `backend/migrations/sql/{004,005}_*.sql` (+down), `backend/migrations/versions/{0004,0005}_*.py`, `backend/tests/integration/test_security_roles.py`
**Size:** M

### - [ ] T05: Telegram `initData` auth + admin guard
**Description:** `POST /api/v1/auth/telegram` verifies `initData` per Telegram spec (HMAC-SHA256 with `WebAppData` key, constant-time compare, `auth_date` freshness window, parse `user.id`), checks `admin_users`, returns signed session token (12 h). FastAPI dependency `require_admin` for all privileged routers. CLI/command to bootstrap the admin Telegram ID. Rate-limit failed attempts (in-process).
**Acceptance criteria:**
- [ ] Valid initData for admin → 200 + token; tampered hash, stale `auth_date`, non-admin → 401/403
- [ ] Privileged route without/with expired/forged token → 401
- [ ] initData and tokens never appear in logs (test captures log output)
**Verification:** standard + `rtk test python -m pytest tests/unit/test_telegram_auth.py tests/integration/test_auth_api.py`
**Dependencies:** T02 (admin_users), T01
**Files:** `backend/clipforge/auth/{telegram,session,deps}.py`, `backend/clipforge/api/v1/auth.py`, `backend/clipforge/admin_cli.py`, tests
**Size:** M

### - [ ] T06: `/health/ready`, error format, request logging
**Description:** Readiness checks DB (`SELECT 1` with timeout), workspace dir writable, free disk ≥ reserve, `ffmpeg`/`ffprobe` on PATH. Consistent JSON error envelope (`{"error":{"code","message","details"}}`) via exception handlers; request ID middleware; access logs with redaction.
**Acceptance criteria:**
- [ ] Ready returns 200 when all checks pass; 503 with failing check names otherwise
- [ ] Validation errors and domain errors use the envelope
**Verification:** standard + `rtk test python -m pytest tests -k "health or errors"`
**Dependencies:** T02
**Files:** `backend/clipforge/api/v1/health.py`, `backend/clipforge/api/errors.py`, `backend/clipforge/api/middleware.py`, tests
**Size:** S

### - [ ] T07: API systemd unit, env template, VPS bootstrap
**Description:** `deploy/systemd/clipforge-api.service` (User=clipforge, `EnvironmentFile=/etc/clipforge-bot/backend.env`, `ExecStart=/opt/clipforge-bot/backend/.venv/bin/uvicorn clipforge.main:app --host 127.0.0.1 --port 8000 --workers 1`, hardening: `NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/clipforge-bot`, `PrivateTmp`). `deploy/backend.env.example` with placeholders only. `deploy/install.sh`: spec Phase 1 apt/user/dirs + install uv, `uv venv --python 3.12 /opt/clipforge-bot/backend/.venv` as the service user + `uv pip install .` + save `uv pip freeze` to the release log + `alembic upgrade head` (migration URL) — idempotent.
**Acceptance criteria:**
- [ ] `systemd-analyze verify` passes on the unit (run on VPS)
- [ ] Re-running `install.sh` is a no-op on a provisioned host
- [ ] No real secrets in repo (`.env.example` placeholders only)
**Verification:** on VPS: `sudo systemctl enable --now clipforge-api` → `curl 127.0.0.1:8000/api/v1/health/ready` = 200; locally `rtk err shellcheck deploy/install.sh` if available
**Dependencies:** T06
**Files:** `deploy/systemd/clipforge-api.service`, `deploy/backend.env.example`, `deploy/install.sh`
**Size:** S

### - [ ] T45: GitHub Actions CI *(do right after T02; numbered last to keep IDs stable)*
**Description:** `.github/workflows/ci.yml` on push/PR to `main`. Backend job: `astral-sh/setup-uv` (pinned version) → `uv venv --python 3.12` → `uv pip install -e "./backend[dev]"` → `apt-get install ffmpeg` → ruff check + format check, mypy, pytest with a `postgres:17` service (`CLIPFORGE_TEST_DATABASE_URL`). Also fails if any direct dependency in `pyproject.toml` lacks an exact `==` pin. Frontend job added when T34 lands (`npm ci`, lint, typecheck, vitest, build). Excludes `-m live`; no secrets needed. Pin third-party actions to commit SHAs.
**Acceptance criteria:**
- [ ] CI green on a PR containing T01–T02; a deliberate lint/test failure turns it red
- [ ] Unpinned dependency in `pyproject.toml` detected
- [ ] No provider/API secrets referenced by the workflow
**Verification:** `rtk gh run list --limit 3`; `rtk gh run view <id>` shows both jobs passing
**Dependencies:** T02
**Files:** `.github/workflows/ci.yml`
**Size:** S

### Checkpoint M1
- [ ] CI green on `main`
- [ ] All tests green; migrations 001–005 applied to Supabase `clipforge-bot` via migration role
- [ ] API runs under systemd on VPS; admin login via real Telegram initData succeeds, non-admin rejected
- [ ] Human review of schema and security model before Phase 2

---

## Phase 2 — Queue, Discovery, Ingestion (M2)

### - [ ] T08: Job queue core (claim / lease / fence / retry / events)
**Description:** `clipforge/queue/` repository: `enqueue(video_id, idempotency_key)` (returns existing job on same key), `claim_next(worker_id)` using `FOR UPDATE SKIP LOCKED` over eligible jobs (`queued`/retry-due or lease expired), sets new `lease_token`, `lease_version+1`, `lease_expires_at=now()+5m`; `renew(lease)`; `fenced_update(lease, **fields)` raising `LeaseLost` on 0 rows; `complete/fail/schedule_retry` with backoff 1/5/15 min + jitter and `PermanentError` → `failed` without retry; append `job_events` for each transition.
**Acceptance criteria:**
- [ ] Two concurrent claimers never get the same job (integration test with 2 connections)
- [ ] After lease expiry another worker reclaims; the stale lease's write raises `LeaseLost` and changes nothing
- [ ] Permanent errors are not retried; transient ones retry ≤ max_attempts with expected delays (clock injected)
**Verification:** standard + `rtk test python -m pytest tests/integration/test_queue.py`
**Dependencies:** T03
**Files:** `backend/clipforge/queue/{repository,errors,backoff}.py`, `backend/tests/integration/test_queue.py`, `backend/tests/unit/test_backoff.py`
**Size:** M

### - [ ] T09: Worker process skeleton
**Description:** `python -m clipforge.workers.main`: loop claim → run stage pipeline → idle poll with backoff. Heartbeat asyncio task renews lease every 60 s; on `LeaseLost` cancels current stage. Stage registry interface (`async run(ctx) -> StageResult`) with "skip if durable artifact exists and checksum verifies" helper. Honors `cancel_requested` between and within stages. SIGTERM/SIGINT → finish/abort current step, release nothing (lease expires naturally) and exit 0. Single job at a time.
**Acceptance criteria:**
- [ ] With fake stages, a job advances through all stages and completes; events recorded
- [ ] Killing the worker mid-stage and restarting resumes from the last completed stage
- [ ] Cancellation request stops the job at the next checkpoint → `cancelled`
**Verification:** standard + `rtk test python -m pytest tests/integration/test_worker.py`
**Dependencies:** T08
**Files:** `backend/clipforge/workers/main.py`, `backend/clipforge/pipeline/{runner,context,stage}.py`, tests
**Size:** M

### - [ ] T10: YouTube Data API client + discovery service
**Description:** httpx client (API key from env) for `videos.list(chart=mostPopular)`, `channels.list(contentDetails)`, `playlistItems.list`, batched `videos.list(part=snippet,contentDetails,statistics)`; ISO-8601 duration parsing; filter 30–120 min; upsert `videos` on `youtube_video_id`, link `video_sources`; quota-aware (unit counter logged per run); transient error retry. Trending algorithm (ADR-019): full `regionCode=ID` chart (4 × 50) → count `categoryId` → top 5 non-excluded categories → each category chart (50, skip on 404 "chart not supported") → duration filter → rank new eligible by views/hour → keep ≤ 20. Category names from `videoCategories.list(regionCode=ID)` cached for the UI. Recorded JSON fixtures, no live calls in tests.
**Acceptance criteria:**
- [ ] Running discovery twice on same fixtures creates no duplicate videos; second source surfacing same video adds only a `video_sources` row
- [ ] Top categories are derived from the fixture chart (not hard-coded); excluded categories skipped; a 404 category chart doesn't fail the run; ≤ 20 new eligible videos kept; run uses ≤ 12 quota units
- [ ] Videos outside 30–120 min are stored as ineligible (or skipped — per decision) and never auto-queued
- [ ] Discovered videos default to `rights_status='unverified'` / `awaiting_source`
**Verification:** standard + `rtk test python -m pytest tests -k discovery`
**Dependencies:** T03
**Files:** `backend/clipforge/discovery/{youtube_client,service,duration}.py`, `backend/tests/fixtures/youtube/*.json`, tests
**Size:** M

### - [ ] T11: Sources/videos/discovery API, CLI, timer
**Description:** `GET/POST /sources`, `PATCH/DELETE /sources/{id}` (DELETE = disable), `POST /discovery/run` (runs in background task with a DB advisory lock to prevent overlap), `GET /videos` (filters + pagination). `python -m clipforge.discovery_cli` uses the same service. `clipforge-discovery.service` (oneshot) + `.timer` (`OnCalendar=*-*-* 03:00:00`, `Persistent=true`).
**Acceptance criteria:**
- [ ] CRUD validated (channel ID format, region code), admin-only
- [ ] Concurrent manual + timer runs don't overlap (second returns "already running")
- [ ] CLI exits non-zero on failure (systemd shows failed)
**Verification:** standard + `rtk test python -m pytest tests -k "sources or discovery_api"`; VPS: `systemctl list-timers clipforge-discovery.timer`
**Dependencies:** T10, T05
**Files:** `backend/clipforge/api/v1/{sources,videos,discovery}.py`, `backend/clipforge/discovery_cli.py`, `deploy/systemd/clipforge-discovery.{service,timer}`, tests
**Size:** M

### - [ ] T12: Media validation + disk admission
**Description:** `media/probe.py` async ffprobe wrapper → typed `MediaInfo`; validator: container/codec allowlist, has audio, duration 30–120 min, max resolution/bitrate sanity. `ingest/disk.py`: projected-usage estimate (source size + audio ≈ duration×bitrate + 3 clips) vs free space minus 5 GB reserve; returns admit / defer / reject.
**Acceptance criteria:**
- [ ] Generated fixtures: valid file passes; no-audio, too-short, corrupt file → `PermanentError` with reason
- [ ] Admission rejects when projected use would breach reserve (free space injected)
**Verification:** standard + `rtk test python -m pytest tests -k "probe or admission"`
**Dependencies:** T01
**Files:** `backend/clipforge/media/probe.py`, `backend/clipforge/ingest/{validation,disk}.py`, `backend/tests/fixtures/make_media.py`, tests
**Size:** M

### - [ ] T13: Chunked upload API with rights evidence
**Description:** `POST /uploads` (video_id, filename, total size, sha256, permission evidence: type + statement + optional reference; admission check) → upload record + staging path. `PATCH /uploads/{id}` with `Content-Range`, ≤ 8 MiB, append at exact offset (409 on mismatch, returns current offset for resume). `POST /uploads/{id}/complete` verifies size + sha256, runs T12 validation, marks `media_assets` ready, sets video `rights_status='authorized'`.
**Acceptance criteria:**
- [ ] Interrupted upload resumes from server-reported offset and completes
- [ ] Wrong checksum, oversize chunk, missing evidence → rejected; staging file removed on failure
- [ ] Staging dir permissions 0700; filenames never used as paths (UUID names)
**Verification:** standard + `rtk test python -m pytest tests -k uploads`
**Dependencies:** T12, T05
**Files:** `backend/clipforge/api/v1/uploads.py`, `backend/clipforge/ingest/uploads.py`, tests
**Size:** M

### - [ ] T14: Permitted URL registration + SSRF-safe download stage
**Description:** `POST /videos/{id}/media` registers an https URL + permission evidence; the host must match `app_settings.permitted_download_domains` (exact host or `*.suffix`; empty list ⇒ URL ingestion disabled with a clear error). YouTube hosts (`youtube.com`, `youtu.be`, `googlevideo.com`, subdomains) are rejected and can't be added to the allowlist (enforced in T30 validation). `security/url_guard.py` resolves DNS and blocks private/loopback/link-local/multicast/reserved IPs on every redirect hop; worker `ingest` stage streams download with size cap, timeout, sha256, then T12 validation.
**Acceptance criteria:**
- [ ] `http://`, `file://`, IP-literal private addresses, redirect to `127.0.0.1`, YouTube URLs, non-allowlisted host, redirect to a non-allowlisted host → rejected; `*.example.com` matches `cdn.example.com` but not `example.com.evil.net`
- [ ] Download over max size aborts and cleans up; success persists checksum
- [ ] Download stage is skipped on restart if artifact exists and checksum matches
**Verification:** standard + `rtk test python -m pytest tests -k "url_guard or download"`
**Dependencies:** T09, T12
**Files:** `backend/clipforge/security/url_guard.py`, `backend/clipforge/ingest/download.py`, `backend/clipforge/pipeline/stages/ingest.py`, `backend/clipforge/api/v1/videos.py`, tests
**Size:** M

### - [ ] T15: Jobs API
**Description:** `POST /videos/{id}/jobs` requires `Idempotency-Key` header (same key → same job, 200; new key while an active job exists → 409); job refuses to start without an authorized ready media asset (`awaiting_source`). `GET /jobs` (filters, pagination), `GET /jobs/{id}` (stage, attempts, events, cost), `POST /jobs/{id}/retry` (failed → queued, resets attempts), `POST /jobs/{id}/cancel` (sets `cancel_requested`).
**Acceptance criteria:**
- [ ] Idempotency semantics as above; missing header → 400
- [ ] Retry on a non-failed job → 409; cancel on finished job → 409
**Verification:** standard + `rtk test python -m pytest tests -k jobs_api`
**Dependencies:** T08, T05
**Files:** `backend/clipforge/api/v1/jobs.py`, `backend/clipforge/api/v1/schemas/jobs.py`, tests
**Size:** S

### Checkpoint M2
- [ ] All tests green; on VPS: timer fires discovery, manual run works, no duplicates across runs
- [ ] An uploaded file and a permitted URL each reach `validated`; unauthorized/YouTube URLs refused
- [ ] Review with human

---

## Phase 3 — AI Transcription & Viral Analysis (M3)

### - [ ] T16: Audio extraction + chunking stage
**Description:** ffmpeg (async subprocess) → mono 16 kHz compressed audio; split into ≤ 10 min chunks with ~2 s overlap; manifest JSON with absolute offsets + checksums persisted as stage artifact.
**Acceptance criteria:**
- [ ] 25-min fixture → 3 chunks with correct offsets; each under provider size limit
- [ ] Re-run reuses manifest when checksums match
**Verification:** standard + `rtk test python -m pytest tests -k audio`
**Dependencies:** T09, T12
**Files:** `backend/clipforge/media/audio.py`, `backend/clipforge/pipeline/stages/audio.py`, tests
**Size:** S

### - [ ] T17: AI provider protocols, registry, settings snapshot, cost tracking
**Description:** `TranscriptResult` (segments + words with absolute seconds, language), `ClipCandidate` (start, end, six dimension scores, title, hook_line, topic, keywords, explanation). Protocols per spec + `EmbeddingProvider`. `SettingsSchema` (ADR-026) resolves `app_settings` + selected model per capability from `ai_provider_configs` + env secret refs; the resolved settings **and prices** are snapshotted into `processing_jobs.settings_snapshot` at claim. `record_usage(ctx, call)` writes `provider_usage` (job, stage, provider, model, latency, input/output tokens or audio seconds, `estimated_cost` = usage × snapshot price) — ADR-024. `FakeTranscriptionProvider`/`FakeAnalysisProvider` report deterministic usage. **Quota guard (ADR-028):** per provider+model RPM token bucket + daily request count from `provider_usage` against `rpd_limit`, reset per `quota_reset`; `reserve(n_calls)` before a stage, else `QuotaDeferred(next_reset)` → job rescheduled (not an attempt); 429 honors `Retry-After`. `GET /ai/providers` lists providers, models, capabilities, enabled flag, prices and today's quota usage (never secrets).
**Acceptance criteria:**
- [ ] *(Should-Have: configurable models)* Changing the selected model or thresholds mid-job doesn't affect the running job; the next job uses the new values
- [ ] *(Should-Have: cost tracking)* Cost math unit-tested for token-priced and audio-minute-priced providers; editing a price later doesn't change existing `provider_usage` rows
- [ ] Missing secret env var for a selected provider → clear config error at readiness, not a mid-job crash; `/ai/providers` contains no secret values
- [ ] Quota guard: with `rpd_limit` reached, the job moves to the next reset time without consuming an attempt; Pacific-midnight reset computed correctly across DST (clock injected)
**Verification:** standard + `rtk test python -m pytest tests -k "providers or usage or cost or settings_snapshot"`
**Dependencies:** T08
**Files:** `backend/clipforge/ai/{types,protocols,registry,usage}.py`, `backend/clipforge/settings_schema.py`, `backend/clipforge/ai/adapters/fake.py`, `backend/clipforge/api/v1/ai.py`, tests
**Size:** M

### - [ ] T18: Gemini transcription adapter + timestamp normalization
**Description:** `GeminiTranscriptionProvider` for `gemini-3.5-transcribe` (file mode, word-level timestamps on, diarization off, language auto-detect with `id-ID` hint) via the official `google-genai` SDK or REST over httpx — pick after checking current docs; upload chunk via Files API if inline size is exceeded. Timeouts, retries mapped to `TransientError`, 429 → quota guard. Chunk length from settings (default 20 min, validated ≤ 28 min). Fallback adapter path: `gemini-3.5-flash-lite` with audio input returning timestamped segments (segment-level timing) when the transcribe model is unavailable. `transcribe` stage: per-chunk calls (sequential), persist each chunk result (resume mid-way), then merge: offset → absolute time, drop overlap duplicates, monotonic timestamps, segments re-built on sentence boundaries.
**Acceptance criteria:**
- [ ] Merge unit tests: overlapping chunk words de-duplicated; timestamps monotonic and within media duration
- [ ] Adapter tested with `respx` recorded responses incl. 429/5xx retry
- [ ] Restart after 2 of 3 chunks re-calls only chunk 3
- [ ] One `provider_usage` row per chunk call with audio seconds + estimated cost (retries recorded too)
**Verification:** standard + `rtk test python -m pytest tests -k transcri`; opt-in `python -m pytest -m live -k transcri`
**Dependencies:** T16, T17
**Files:** `backend/clipforge/ai/adapters/gemini_transcription.py`, `backend/clipforge/ai/timestamps.py`, `backend/clipforge/pipeline/stages/transcribe.py`, tests
**Size:** M

### - [ ] T19: Scoring + candidate selection (pure)
**Description:** `score(dims) = 0.25H+0.20E+0.20C+0.15R+0.10V+0.10F` (inputs validated 0–100; weights fixed by spec). `select(candidates, transcript, settings)`: drop < `min_score`, snap start/end to segment boundaries, clamp to `clip_min_s`–`clip_max_s` (discard if impossible), greedy by score with pairwise overlap ≤ `max_overlap` of the shorter clip, return ≤ `max_clips`. All thresholds come from the job's settings snapshot (defaults 75 / 20 s / 60 s / 0.20 / 3).
**Acceptance criteria:**
- [ ] Table-driven tests: formula, threshold edge (74.99 vs 75), overlap 19 % vs 21 %, duration clamps, max 3
- [ ] *(Should-Have: configurable thresholds/durations)* Non-default settings (e.g. `min_score=80`, 30–45 s, `max_clips=2`) change the output accordingly
- [ ] Property test: output never violates duration/overlap/count constraints
**Verification:** standard + `rtk test python -m pytest tests/unit/test_scoring.py`
**Dependencies:** T17 (types only)
**Files:** `backend/clipforge/scoring/{score,select}.py`, `backend/tests/unit/test_scoring.py`
**Size:** S

### - [ ] T20: Frame sampling stage
**Description:** Extract low-res (e.g. 360 px wide) JPEG keyframes at a fixed interval plus scene-change frames (ffmpeg `select='gt(scene,0.3)'`), capped per minute; index with timestamps; persisted artifact.
**Acceptance criteria:**
- [ ] Fixture with scene cuts yields frames near cut times; total frames bounded
- [ ] Memory stays low (ffmpeg does the work; Python only lists files)
**Verification:** standard + `rtk test python -m pytest tests -k frames`
**Dependencies:** T09
**Files:** `backend/clipforge/media/frames.py`, `backend/clipforge/pipeline/stages/frames.py`, tests
**Size:** S

### - [ ] T21: Gemini analysis adapter + OpenRouter fallback (windowed)
**Description:** `GeminiAnalysisProvider` (window pass on `gemini-3.5-flash-lite` with `response_schema` structured output; re-rank pass with frames on `gemini-3-flash-preview`, auto-downgrading to flash-lite when its quota is exhausted) and `OpenRouterAnalysisProvider` (OpenAI-compatible chat completions, `nvidia/nemotron-3.5-super-vl:free`, JSON enforced by prompt + Pydantic validation since free models may lack JSON mode) used when Gemini is unavailable or out of quota. Prompts handle Indonesian transcripts and ask for titles in the transcript's language. Windowing (ADR-011): transcript split into ~8–10 min overlapping windows → per-window candidate proposals (structured JSON, Pydantic-validated, one repair retry) → shortlist → re-rank pass with frames for shortlisted ranges (V score). Prompts versioned in code; context includes video title/channel/language. Clamp timestamps to transcript bounds.
**Acceptance criteria:**
- [ ] Malformed model output → repair retry → else `TransientError`; out-of-range timestamps clamped/dropped
- [ ] Recorded-response tests produce valid `ClipCandidate`s; usage recorded per call
- [ ] Frames sent only for shortlisted windows
- [ ] Gemini 429/quota-exhausted on window pass → OpenRouter fallback produces schema-valid candidates (recorded responses); both unavailable → `QuotaDeferred`
- [ ] *(Should-Have: generated titles)* Each candidate has a non-empty `title` (≤ 70 chars, transcript language, grounded in the segment — prompt forbids claims absent from the transcript) and `hook_line`; over-length titles are truncated at a word boundary
- [ ] Each window call and the re-rank call write `provider_usage` with tokens + estimated cost
**Verification:** standard + `rtk test python -m pytest tests -k analysis`; opt-in live test
**Dependencies:** T17, T18, T20
**Files:** `backend/clipforge/ai/adapters/{gemini_analysis,openrouter_analysis}.py`, `backend/clipforge/ai/prompts/analysis.py`, `backend/clipforge/ai/windowing.py`, tests
*(If this grows past M, split the OpenRouter fallback into its own task.)*
**Size:** M

### - [ ] T22: Wire analysis pipeline stages
**Description:** Stages `transcribe → frames → analyze → select` persist transcript (artifact), raw candidates and selected candidates in `clip_candidates` (scores, dims, explanation, generated title + hook line, selected flag). Job total estimated cost = sum of its `provider_usage` rows, exposed via the job repository. No candidate ≥ threshold → `completed_no_clips`. E2E test with fake providers on a generated fixture.
**Acceptance criteria:**
- [ ] Fake-provider E2E: job reaches `candidates_selected` with ≤ 3 valid candidates
- [ ] All-low scores → `completed_no_clips`, no render stage run
**Verification:** standard + `rtk test python -m pytest tests/e2e/test_pipeline_analysis.py`
**Dependencies:** T19, T21, T14
**Files:** `backend/clipforge/pipeline/stages/{analyze,select}.py`, `backend/clipforge/pipeline/registry.py`, tests
**Size:** M

### Checkpoint M3
- [ ] One real 60–120 min authorized video → valid, timestamped, scored candidates; cost and per-stage latency in `provider_usage`/`job_events`
- [ ] Human spot-check of candidate quality; tune prompts/thresholds before rendering work

---

## Phase 4 — Rendering, Drive, Notifications (M4)

### - [ ] T23: ASS subtitle generation
**Description:** From word timestamps within the clip range, build ASS subtitles (re-based to clip start), max 2 lines, ~32 chars/line, safe-area margins for 720×1280, readable font/outline; escape ASS special characters.
**Acceptance criteria:**
- [ ] Golden-file tests for timing re-base, wrapping, escaping
**Verification:** standard + `rtk test python -m pytest tests -k subtitles`
**Dependencies:** T18
**Files:** `backend/clipforge/media/subtitles.py`, tests
**Size:** S

### - [ ] T24: Render stage + output verification
**Description:** Per selected candidate (sequential): ffmpeg seek (`-ss` before `-i` + accurate trim) → center crop/scale to 720×1280 → burn subtitles → libx264 `-threads 2` + AAC, `+faststart`. ffprobe verifies 720×1280, h264/aac, duration within ±0.5 s; persist `clips` (incl. `title` copied from the candidate) + `clip_artifacts` (size, sha256). Drive file name is derived from a sanitized title + short clip id. Deterministic output path from clip id; skip if verified artifact exists.
**Acceptance criteria:**
- [ ] Fixture renders produce compliant files; duration 20–60 s
- [ ] Restart mid-render re-renders only unfinished clips
**Verification:** standard + `rtk test python -m pytest tests -k render`
**Dependencies:** T22, T23
**Files:** `backend/clipforge/media/render.py`, `backend/clipforge/pipeline/stages/render.py`, tests
**Size:** M

### - [ ] T25: Google Drive OAuth + status
**Description:** `GET /integrations/drive/connect` (admin) → Google auth URL with single-use `state` stored server-side; `GET /integrations/drive/callback` (unauthenticated but state-validated) exchanges code, encrypts refresh token (Fernet key from env) into `oauth_credentials`; `GET /integrations/drive/status` reports connected/expired/scope + target folder. Token refresh helper. Scope `drive.file`.
**Acceptance criteria:**
- [ ] Invalid/reused state → 400; token stored encrypted (DB value ≠ plaintext)
- [ ] Status never returns token material
**Verification:** standard + `rtk test python -m pytest tests -k drive_oauth` (Google endpoints mocked)
**Dependencies:** T05
**Files:** `backend/clipforge/storage/drive_oauth.py`, `backend/clipforge/security/crypto.py`, `backend/clipforge/api/v1/integrations.py`, tests
**Size:** M

### - [ ] T26: Drive resumable upload + reconciliation
**Description:** Upload stage (ADR-014): look up existing file by `appProperties.clipforge_clip_id` → adopt if size/md5 match; else resume persisted session URI (query offset) or initiate new resumable session, persist URI, stream in chunks; verify `size` + `md5Checksum`; persist `drive_file_id`, `webViewLink`; then delete local clip. Private sharing only. Handle 308/410/5xx per Drive docs.
**Acceptance criteria:**
- [ ] Simulated crash after Drive completed but before DB persist → rerun adopts existing file (no duplicate)
- [ ] Simulated crash mid-upload → resumes from offset
- [ ] Local file only deleted after verification; mismatch → re-upload, never delete
**Verification:** standard + `rtk test python -m pytest tests -k drive_upload`; opt-in `-m live` against a test folder
**Dependencies:** T24, T25
**Files:** `backend/clipforge/storage/drive.py`, `backend/clipforge/pipeline/stages/upload.py`, tests
**Size:** M

### - [ ] T27: Telegram notifications
**Description:** Bot API `sendMessage` to admin chat on: clips ready for review (listing generated titles, scores and job estimated cost), job failed (permanent / retries exhausted), discovery summary, Drive disconnected. Failures to notify are logged, never fail the job. Toggle in settings.
**Acceptance criteria:**
- [ ] Message templates contain no secrets/URLs with tokens; notify failure doesn't change job state
**Verification:** standard + `rtk test python -m pytest tests -k notify`
**Dependencies:** T26
**Files:** `backend/clipforge/notify/telegram.py`, `backend/clipforge/pipeline/stages/notify.py`, tests
**Size:** S

### - [ ] T28: Workspace cleanup + worker unit
**Description:** Per-job workspace `/var/lib/clipforge-bot/workspace/<job_id>/`; after completion delete source/audio/frames (keep transcript JSON in DB or small artifact); startup GC removes dirs of terminal/missing jobs older than N hours; never delete un-uploaded clips. `clipforge-worker.service` with `MemoryMax=1800M`, `CPUQuota=170%`, `Restart=always`, `RestartSec=10`, hardening as T07.
**Acceptance criteria:**
- [ ] GC test: only safe-to-delete dirs removed; unverified clip artifacts kept
- [ ] Worker unit verified on VPS; restart after `kill -9` resumes job
**Verification:** standard + `rtk test python -m pytest tests -k cleanup`; VPS: `systemctl status clipforge-worker`
**Dependencies:** T26
**Files:** `backend/clipforge/pipeline/cleanup.py`, `deploy/systemd/clipforge-worker.service`, tests
**Size:** S

### Checkpoint M4 — spec gate before frontend
- [ ] One full 60–120 min video end-to-end on VPS: ≤ 3 clips, 720×1280, verified Drive IDs, Telegram notification
- [ ] `kill -9` worker during upload → no duplicate Drive file after recovery
- [ ] Peak RAM < 1.8 GB, free disk ≥ 5 GB throughout; record timings
- [ ] Human review before Phase 5

---

## Phase 5 — Review API & Affiliate Backend (M5, backend)

### - [ ] T29: Clips API + review audit
**Description:** `GET /clips` (filters: status, video, date), `GET /clips/{id}` (generated title + effective title, hook line, candidate scores/explanation, transcript excerpt, Drive link, artifacts, job cost), `POST /clips/{id}/review` (approve/reject + note + optional `title_override`) writing `clip_reviews` and updating `clips.review_status` / `clips.title_override`.
**Acceptance criteria:**
- [ ] Review is append-only audit; re-review allowed and recorded
- [ ] *(Should-Have: generated titles)* Title override is stored and audited; the generated title is never overwritten; effective title = override ?? generated
- [ ] OpenAPI schema exported to `frontend/src/api/openapi.json` (script)
**Verification:** standard + `rtk test python -m pytest tests -k clips_api`
**Dependencies:** T26
**Files:** `backend/clipforge/api/v1/clips.py`, `backend/clipforge/api/v1/schemas/clips.py`, `backend/scripts/export_openapi.py`, tests
**Size:** M

### - [ ] T30: Settings, cost and dashboard API
**Description:** `GET/PATCH /settings` backed by `SettingsSchema` (ADR-026): scoring threshold, clip durations, max clips, overlap, models per capability, per-provider prices, timeouts/retry limits, notifications, discovery config, affiliate toggle/threshold/disclosure; unknown keys rejected; each change audited (old → new, admin, time) in an `app_settings` history/event. `GET /dashboard`: counts by job status, clips awaiting review, last discovery run, disk free, and **AI cost** (estimated) for 7 and 30 days broken down by day, provider/model and stage, plus average cost per completed job.
**Acceptance criteria:**
- [ ] `permitted_download_domains` entries validated (hostname or `*.suffix`, no IPs, no schemes/paths, YouTube/Google-video hosts refused); discovery `region_code` is ISO 3166-1 alpha-2
- [ ] *(Should-Have: configurable)* Invalid values → 422: `clip_min_s > clip_max_s`, durations outside 20–60 s (spec bounds), `max_clips` outside 1–3, `min_score` outside 0–100, `max_overlap` outside 0–0.20, model not present/enabled in `ai_provider_configs` for that capability
- [ ] *(Should-Have: cost tracking)* Cost aggregates match the sum of `provider_usage` rows in a seeded test
- [ ] Dashboard query is a bounded number of SQL statements
**Verification:** standard + `rtk test python -m pytest tests -k "settings or dashboard"`
**Dependencies:** T17, T29
**Files:** `backend/clipforge/settings_service.py`, `backend/clipforge/api/v1/{settings,dashboard}.py`, tests
**Size:** M

### - [ ] T31: `006_affiliate_links.sql` + catalog API
**Description:** Migration exactly per spec (plus `updated_at` trigger, grants/RLS for new tables). `GET/POST /affiliate/products`, `PATCH/DELETE /affiliate/products/{id}` (DELETE = deactivate). Validation: `https://` only, host must equal `destination_domain` or be in merchant allowlist, block IP literals/private ranges/unsafe schemes; no server-side fetching; tracking params preserved but redacted in logs.
**Acceptance criteria:**
- [ ] `http://`, `javascript:`, `https://10.0.0.1/…`, domain mismatch → 422
- [ ] Partial unique index enforces one approved primary link per clip
**Verification:** standard + `rtk test python -m pytest tests -k "affiliate_catalog or migrations"`
**Dependencies:** T04, T29
**Files:** `backend/migrations/sql/006_affiliate_links.sql` (+down), `backend/migrations/versions/0006_*.py`, `backend/clipforge/affiliate/validation.py`, `backend/clipforge/api/v1/affiliate.py`, tests
**Size:** M

### - [ ] T32: Affiliate matcher + stage + rematch
**Description:** Matcher (ADR-020): inputs = clip transcript segment, topic, keywords, title; filter active products by language/region + tag/category; rank by `gemini-embedding-2` cosine similarity (product embeddings cached, recomputed on product edit) else keyword fallback when embeddings are unavailable/out of quota; threshold (default 0.70); ≤ 3 suggestions; top flagged primary-candidate; `match_reason` stored; `affiliate_events` logged. Worker stage runs only if `affiliate_links_enabled` is true at run time. `POST /clips/{id}/affiliate-links/rematch` (409 when disabled).
**Acceptance criteria:**
- [ ] Toggle off → stage no-op, rematch rejected, zero new rows
- [ ] Relevant product suggested; unrelated/inactive excluded; no match → zero suggestions (no invented links)
**Verification:** standard + `rtk test python -m pytest tests -k affiliate_match`
**Dependencies:** T31, T17
**Files:** `backend/clipforge/affiliate/matcher.py`, `backend/clipforge/pipeline/stages/affiliate.py`, `backend/clipforge/api/v1/affiliate.py`, tests
**Size:** M

### - [ ] T33: Affiliate link review + caption export
**Description:** `GET /clips/{id}/affiliate-links`, `POST /clips/{id}/affiliate-links/{link_id}/review` (approve/reject, set primary, manual product selection), admin can remove records. Caption/description draft builder (always available; affiliate part only when enabled): effective title + hook line + description + only **approved** links + mandatory disclosure; returns no links when toggle off; wording says "related", never "featured". Exposed in clip detail.
**Acceptance criteria:**
- [ ] Rejected or suggested-only links never appear in export; disclosure present whenever ≥ 1 link exported
- [ ] Toggle off → export contains no affiliate URLs even if approved rows exist
**Verification:** standard + `rtk test python -m pytest tests -k "affiliate_review or caption"`
**Dependencies:** T32
**Files:** `backend/clipforge/affiliate/{review,caption}.py`, `backend/clipforge/api/v1/clips.py`, tests
**Size:** M

### Checkpoint: API complete
- [ ] Every endpoint in the spec API table implemented and admin-guarded (route-table test asserts this)
- [ ] Spec affiliate acceptance rules pass as API tests; OpenAPI exported

---

## Phase 6 — Telegram Mini App (M5, frontend)

### - [ ] T34: Frontend scaffold + auth handshake
**Description:** `frontend/` Vite React TS (strict), ESLint, Vitest, React Router, TanStack Query; load `telegram-web-app.js`; on start POST `initData` to `/api/v1/auth/telegram`, keep token in memory; typed API client generated from OpenAPI (or hand-typed from it); Telegram theme CSS vars; `BackButton`/`MainButton` helpers; dev proxy to `127.0.0.1:8000`.
**Acceptance criteria:**
- [ ] Opened outside Telegram → clear "open from Telegram" screen; non-admin → access denied screen
- [ ] Build output is static `dist/` deployable to `/var/www/clipforge-bot`
**Verification:** standard frontend checks; manual: open via BotFather test Mini App URL
**Dependencies:** T05, T29, T30
**Files:** `frontend/{package.json,vite.config.ts,tsconfig.json,index.html}`, `frontend/src/{main.tsx,App.tsx,api/client.ts,auth/telegram.ts}`
**Size:** M

### - [ ] T35: Dashboard + Jobs screens
**Description:** Dashboard stats including AI cost cards (7/30-day estimated totals, by provider/model and by stage, average per job); jobs list (status filter, polling while active), job detail (stage timeline from `job_events` with durations, attempts, errors, per-stage cost breakdown, retry/cancel actions with confirm).
**Acceptance criteria:** [ ] Retry/cancel reflect server state; [ ] loading/error/empty states present; [ ] costs shown with currency and labeled "estimated"
**Verification:** standard frontend checks (component tests with mocked API)
**Dependencies:** T34
**Files:** `frontend/src/pages/{Dashboard,Jobs,JobDetail}.tsx`, `frontend/src/api/jobs.ts`
**Size:** M

### - [ ] T36: Sources, Videos, media registration + chunked upload
**Description:** Source list/create/edit/disable; run discovery; videos list with eligibility/rights status; register permitted URL with evidence form; file upload in 8 MiB chunks with progress, resume, sha256 computed in a Web Worker; queue job with generated Idempotency-Key.
**Acceptance criteria:** [ ] Upload resumes after network drop; [ ] evidence fields required before submit
**Verification:** standard frontend checks; manual upload of a real file via Telegram on mobile
**Dependencies:** T35
**Files:** `frontend/src/pages/{Sources,Videos,VideoDetail}.tsx`, `frontend/src/upload/{chunkedUpload.ts,hashWorker.ts}`
**Size:** L (split upload into its own PR if it grows)

### - [ ] T37: Clip preview & review
**Description:** Clip list (awaiting review first, showing effective titles), detail with "Open in Google Drive" button (`Telegram.WebApp.openLink(webViewLink)`, ADR-029; hint that the admin must be signed in to the uploading Google account), generated title with inline edit (sent as `title_override` on review), hook line, scores breakdown + explanation, transcript excerpt, approve/reject with note.
**Acceptance criteria:** [ ] Review persists and list updates; [ ] edited title shown everywhere, original visible as "AI suggestion"; [ ] "Open in Google Drive" opens the clip externally on Telegram iOS and Android
**Verification:** standard frontend checks; manual on device
**Dependencies:** T35
**Files:** `frontend/src/pages/{Clips,ClipDetail}.tsx`, `frontend/src/api/clips.ts`
**Size:** M

### - [ ] T38: Affiliate UI
**Description:** Settings → Affiliate Links switch (default off), threshold + disclosure text; product catalog CRUD; in clip detail: suggestions with reasons, approve/reject/set primary/manual pick, rematch; copyable caption/description draft (Telegram clipboard / fallback).
**Acceptance criteria:** [ ] With toggle off, suggestions/export UI hidden or disabled and caption has no links; [ ] copy produces exactly the API export text
**Verification:** standard frontend checks; manual flow on device
**Dependencies:** T37, T33
**Files:** `frontend/src/pages/{Affiliate,AffiliateProductForm}.tsx`, `frontend/src/components/AffiliateSuggestions.tsx`, `frontend/src/api/affiliate.ts`
**Size:** M

### - [ ] T39: Settings, providers, Drive connection + Nginx deploy
**Description:** Settings page — *(Should-Have: configurable)* min score, clip min/max duration, max clips, overlap (inputs bounded to the API's limits), model selection per capability from `/ai/providers` (only enabled models offered), per-model prices and RPM/RPD quota limits with today's usage, permitted download domain allowlist editor, discovery settings (region, top-category count, excluded categories by name, max new videos/run), notifications; note that changes apply to new jobs only — Drive status + connect (opens OAuth externally via `Telegram.WebApp.openLink`). `deploy/nginx/clipforge-bot.conf` snippet from spec for inclusion in the existing HTTPS vhost; deploy script copies `dist/`.
**Acceptance criteria:** [ ] Validation errors from API shown inline; [ ] `sudo nginx -t` passes before reload; existing TLS config untouched
**Verification:** standard frontend checks; VPS: `sudo nginx -t && sudo systemctl reload nginx`, open Mini App in Telegram
**Dependencies:** T34, T25, T30
**Files:** `frontend/src/pages/Settings.tsx`, `frontend/src/api/settings.ts`, `deploy/nginx/clipforge-bot.conf`, `deploy/deploy_frontend.sh`
**Size:** M

### Checkpoint M5
- [ ] Admin configures, monitors, previews, approves/rejects; manages affiliate catalog, toggles matching, approves links — all in Telegram
- [ ] Human UX review on a real phone

---

## Phase 7 — Reliability, Hardening, Handover (M6–M7)

### - [ ] T40: Reliability test suite
**Description:** Scripted scenarios (integration + VPS runbook): `kill -9` during each stage; lease expiry with two workers; DB connection drop; provider timeouts/429; Drive 5xx + ambiguous completion; disk-full admission; discovery API failure; cancellation mid-render.
**Acceptance criteria:** [ ] Every scenario either recovers or fails visibly (job `failed` + event + notification), never silent; [ ] 0 duplicate Drive files
**Verification:** `rtk test python -m pytest tests/reliability`; VPS scenario log attached to `docs/reliability-report.md`
**Dependencies:** Checkpoint M5
**Files:** `backend/tests/reliability/*`, `docs/reliability-report.md`
**Size:** M

### - [ ] T41: Security hardening review
**Description:** Run `/security-review`; verify: all privileged routes guarded (route table test), initData replay/staleness, rate limiting, RLS/grants (runtime role cannot DDL, anon blocked), SSRF suite, secrets absent from logs (grep journald during E2E), env file perms (`root:clipforge 0640`), Fernet key rotation procedure documented.
**Acceptance criteria:** [ ] No high/critical findings open; [ ] log audit finds no tokens/initData/refresh tokens
**Verification:** `rtk test python -m pytest -m security`; manual checklist in `docs/security-checklist.md`
**Dependencies:** T40
**Files:** tests, `docs/security-checklist.md`
**Size:** M

### - [ ] T42: Resource benchmark + metrics
**Description:** Script to run N authorized videos and collect per-stage latency, CPU, peak RSS, peak disk, upload duration, AI cost from `job_events`/`provider_usage`/systemd accounting; report sustainable videos/day (5/10/20).
**Acceptance criteria:** [ ] Report produced with numbers for at least 5 real videos; [ ] recommended daily cap set in settings
**Verification:** `python -m clipforge.tools.benchmark_report`; review `docs/benchmark-report.md`
**Dependencies:** T40
**Files:** `backend/clipforge/tools/benchmark_report.py`, `docs/benchmark-report.md`
**Size:** S

### - [ ] T43: Handover documentation
**Description:** `docs/installation.md` (VPS from scratch: Phase 1 commands, uv, env, migrations, systemd, Nginx, BotFather Mini App setup, Google OAuth client, Supabase role creation), `docs/backup-restore.md` (Supabase backups/PITR, env + Fernet key escrow, Drive), `docs/runbook.md` (service ops, logs via journalctl, stuck job handling, retry/cancel, disk pressure, rotating secrets, upgrading with `git pull` + `uv pip install .`, rollback to the previous tag + its recorded `uv pip freeze`). Update graph: `/graphify . --update`.
**Acceptance criteria:** [ ] A fresh VPS install following only the docs succeeds (dry-run by human)
**Verification:** human walkthrough; `graphify query "how do I recover a stuck job"` returns runbook nodes
**Dependencies:** T42
**Files:** `docs/{installation,backup-restore,runbook}.md`
**Size:** M

### - [ ] T44: Production rollout + 7-day pilot
**Description:** Deploy tagged release, enable timer, run pilot on representative authorized videos; track spec pilot metrics table daily (discovery 7/7, success ≥ 90 %, 0 duplicate uploads, 0 unauthorized access, 100 % duration compliance, ≤ 3 clips/source, ≥ 5 GB free, ≥ 70 % usable, affiliate toggle/approval/disclosure 100 %).
**Acceptance criteria:** [ ] All pilot targets met or deviations documented with fixes
**Verification:** `docs/pilot-report.md` with daily metrics
**Dependencies:** T43
**Files:** `docs/pilot-report.md`
**Size:** S

### Checkpoint: Production ready
- [ ] All Must-Have requirements REQ-001…REQ-019 traced to passing tests
- [ ] Security, resource and recovery tests pass; runbooks delivered; system runs unattended

---

## Requirement Traceability

| REQ | Tasks |
|---|---|
| 001 Trending discovery | T10, T11 |
| 002 Channel monitoring | T10, T11 |
| 003 Daily + on-demand | T11 |
| 004 Dedupe videos/jobs | T03, T10, T15 |
| 005 Authorized ingestion | T13, T14 |
| 006 Verify auth/format/disk | T12, T13, T14 |
| 007 Cloud transcription | T16, T17, T18 |
| 008 Transcript + scene analysis | T20, T21, T22 |
| 009 ≤ 3 clips | T19, T24 |
| 010 9:16 MP4 + subtitles | T23, T24 |
| 011 Drive upload + verify | T25, T26 |
| 012 Mini App, single admin | T05, T34–T39 |
| 013 Approve/reject | T29, T37 |
| 014 Persist + recover | T08, T09, T40 |
| 015 VPS / Nginx / systemd | T07, T11, T28, T39 |
| 016 Supabase metadata | T02–T04, T31 |
| 017 Affiliate toggle (default off) | T04, T30, T38 |
| 018 Affiliate matching | T32 |
| 019 Affiliate links + disclosure | T33, T38 |

### Should-Have traceability (included in MVP)

| Should-Have | Tasks |
|---|---|
| Configurable AI models, thresholds, durations | T04 (defaults), T17 (schema + snapshot), T19 (applied), T30 (API + validation), T39 (UI) |
| Generated titles | T21 (generated), T22/T24 (persisted), T29 (override), T33 (captions), T37 (UI) |
| Logs and AI cost tracking | T01/T06 (structured, redacted logs), T17 (usage + cost), T18/T21 (recorded per call), T22 (job total), T30 (aggregates), T35 (UI) |
