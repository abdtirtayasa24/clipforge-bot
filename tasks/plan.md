# Implementation Plan: ClipForge Bot

Source of truth: `docs/SPEC-001-ClipForge-Bot.md` (v1.1). Requirement IDs (`REQ-xxx`) and milestones (`M1`–`M7`) refer to that spec.

## Overview

ClipForge Bot is a single-admin system that discovers long-form YouTube videos (trending + monitored channels), ingests only **creator-authorized** media (uploaded files or permitted URLs), transcribes and analyzes it with configurable cloud AI providers, selects up to three 20–60 s highlights scoring ≥ 75, renders them as 720×1280 H.264/AAC clips with burned subtitles, uploads them to Google Drive with verified, duplicate-free uploads, and presents them for approval in a Telegram Mini App. An optional, default-off affiliate feature suggests admin-curated products per clip and produces disclosure-bearing caption drafts containing only approved links.

It runs on a small VPS (2 vCPU / 4 GB RAM / 20–30 GB disk) as native systemd services behind the existing Nginx, with Supabase PostgreSQL as the only datastore and a Postgres-backed durable job queue. Backend and AI pipeline come first; the full frontend is built only after one 60–120 min video has gone end-to-end (spec Phase 3 gate).

## Working Rules

All contributors follow [`AGENTS.md`](../AGENTS.md) (`CLAUDE.md` points to it): uv tooling, `rtk`, graphify-first navigation, definition of done, naming conventions, code style and the one-line commit format.

## Target Repository Layout

```
clipforge-bot/
├── backend/
│   ├── pyproject.toml            # package "clipforge"; exact-pinned deps + [dev] extra (only dep file)
│   ├── .venv/                    # uv venv (gitignored)
│   ├── alembic.ini
│   ├── migrations/
│   │   ├── env.py
│   │   ├── versions/             # thin Alembic revisions, one per SQL file
│   │   └── sql/                  # 001_initial.sql … 006_affiliate_links.sql (+ *_down.sql)
│   ├── clipforge/
│   │   ├── main.py               # FastAPI app  -> clipforge.main:app
│   │   ├── config.py             # pydantic-settings, env-driven
│   │   ├── db/                   # engine, session, ORM models
│   │   ├── api/v1/               # routers: auth, sources, videos, uploads, jobs, clips, settings, ai, integrations, affiliate, health
│   │   ├── auth/                 # Telegram initData verification, session tokens
│   │   ├── discovery/            # YouTube Data API client + discovery service
│   │   ├── discovery_cli.py      # python -m clipforge.discovery_cli
│   │   ├── ingest/               # uploads, permitted URL download, validation, disk admission
│   │   ├── queue/                # job claiming, leases, fencing, retries, events
│   │   ├── workers/main.py       # python -m clipforge.workers.main
│   │   ├── pipeline/stages/      # one module per stage
│   │   ├── ai/                   # Protocols, registry, adapters/, usage tracking
│   │   ├── scoring/              # weighted score + candidate selection (pure)
│   │   ├── media/                # ffmpeg/ffprobe wrappers, audio chunking, frames, subtitles, render
│   │   ├── storage/drive.py      # OAuth + resumable upload + reconciliation
│   │   ├── notify/telegram.py    # Bot API notifications
│   │   ├── affiliate/            # catalog validation, matcher, caption export
│   │   └── security/             # URL/SSRF guard, token encryption
│   └── tests/{unit,integration,e2e,fixtures}/
├── frontend/                      # Vite + React + TypeScript Mini App
├── deploy/
│   ├── systemd/                   # clipforge-api/worker/discovery .service + .timer
│   ├── nginx/clipforge-bot.conf   # snippet to include in existing vhost
│   ├── backend.env.example
│   └── install.sh
├── .github/workflows/ci.yml       # lint + typecheck + tests (backend w/ Postgres service, frontend)
├── docs/
│   ├── SPEC-001-ClipForge-Bot.md
│   ├── adr/                       # README.md index + adr-001.md … adr-030.md
│   └── …                          # install guide, runbook, backup/restore (T43)
├── tasks/                         # plan.md, todo.md
├── AGENTS.md                      # contributor rules (single source)
└── CLAUDE.md                      # points to AGENTS.md
```

## Architecture Decisions

All architecture decisions are recorded as ADRs in [`docs/adr/`](../docs/adr/README.md) (`adr-001.md` … `adr-030.md`). Tasks reference them as `ADR-NNN`. The free-tier AI provider choice, expected call volume, OpenRouter limits and the free-tier data-use trade-off are in [ADR-027](../docs/adr/adr-027.md); the quota guard is [ADR-028](../docs/adr/adr-028.md).

## Decisions on Former Open Questions (2026-10-19)

| # | Question | Decision |
|---|---|---|
| 1 | AI providers | Free tier: Gemini primary + OpenRouter fallback (ADR-027) |
| 2 | Test database | Local PostgreSQL 17, separate `clipforge_test` DB (ADR-023) |
| 3 | Permitted URLs | Admin-maintained domain allowlist (ADR-018) |
| 4 | Clip preview | Open the Drive file externally (ADR-029) |
| 5 | Discovery | Region `ID`; top 5 categories from the current 200-video trending chart; 50 per category chart; keep ≤ 20 newly eligible videos/run (ADR-019) |
| 6 | CI | GitHub Actions (ADR-030, task T45) |
| 7 | `graphify-out/` | Local only, gitignored |
| — | Tooling revision | `uv venv` + `uv pip install -e ".[dev]"`; `pyproject.toml` is the only dependency file (ADR-001, AGENTS.md) |

## Should-Have Scope Included

These spec Should-Have items are in scope for the MVP. They are built inside the existing tasks rather than as separate phases.

| Should-Have | What is delivered | Backend tasks | UI tasks |
|---|---|---|---|
| **Configurable AI models, thresholds, durations** | Typed settings schema (ADR-026); model choice per capability validated against enabled `ai_provider_configs`; `min_score` (default 75), `clip_min_s`/`clip_max_s` (20/60), `max_clips` (≤ 3, spec hard cap), `max_overlap` (0.20); job snapshot so edits affect new jobs only; settings changes audited | T04, T17, T19, T30 | T39 |
| **Generated titles** | Title + hook line produced by the analysis pass (ADR-025), persisted per candidate and clip, admin override at review, used in caption/description export and Telegram notifications | T21, T22, T24, T29, T33 | T37 |
| **Logs and AI cost tracking** | Structured JSON logs with request/job/stage context and secret redaction (ADR-022); per-call `provider_usage` with estimated cost from price snapshot (ADR-024); per-job total cost; dashboard cost by day / provider / stage over 7 and 30 days; job timeline from `job_events` | T01, T06, T17, T18, T21, T30 | T35 |

Explicitly **not** included (stays Could-Have / out of scope): hashtag generation, alternative title variants, hard spending caps that stop jobs, external log shipping.

## Dependency Graph

```
uv project + config + logging
   └── DB engine + Alembic(SQL) migrations 001–005 + ORM models
         ├── Telegram auth + admin guard ──────────────────────────────┐
         ├── Job queue (claim/lease/fence/retry/events)                │
         │     └── Worker loop + stage runner + cancellation           │
         │           ├── Ingest (uploads API, URL download, validation, disk admission)
         │           │     └── Audio extract/chunk
         │           │           └── Transcription adapter + timestamp normalization
         │           │                 └── Frame sampling + Analysis adapter
         │           │                       └── Scoring/selection (pure)
         │           │                             └── Subtitles + Render
         │           │                                   └── Drive OAuth + resumable upload + reconcile
         │           │                                         └── Notifications, cleanup
         │           │                                               └── Affiliate matching (006)
         ├── YouTube discovery + sources/videos API + discovery CLI    │
         └── AI provider registry + usage tracking                     │
                                                                       ▼
                                     REST API v1 complete ──► Telegram Mini App
systemd units + Nginx snippet + install script (needed from M1, completed in M4/M7)
```

## Phase / Task Summary

Detailed tasks with acceptance criteria and verification live in `tasks/todo.md`.

### Phase 1 — Foundation (M1)
- T01 Backend uv project scaffold, config, logging, `/health/live`
- T02 DB layer + Alembic-runs-SQL + `001_initial.sql` core entities
- T03 `002_job_recovery.sql` + `003_artifacts.sql` + ORM models + schema-drift test
- T04 `004_security.sql` (roles, grants, RLS, indexes) + `005_settings.sql` (defaults, provider configs)
- T05 Telegram `initData` auth, session tokens, admin guard
- T06 `/health/ready`, error format, request logging with redaction
- T07 `clipforge-api.service`, `backend.env.example`, VPS bootstrap script (spec Phase 1 + API unit)
- T45 GitHub Actions CI (runs right after T02, extended as tasks land)

**Checkpoint M1:** API runs under systemd on VPS, migrations apply on Supabase, admin auth succeeds, non-admin rejected.

### Phase 2 — Queue, Discovery, Ingestion (M2)
- T08 Job queue core: idempotent create, claim/lease/fence, retry/backoff, events
- T09 Worker process: loop, heartbeat, stage runner, cancellation, graceful shutdown
- T10 YouTube Data API client + discovery service with dedupe
- T11 Sources/videos/discovery API + `discovery_cli` + discovery systemd unit/timer
- T12 Media validation (ffprobe) + disk admission control
- T13 Chunked upload API (init / PATCH / complete) with rights evidence
- T14 Permitted-URL registration + SSRF-safe worker download stage
- T15 Jobs API: queue (idempotency key), list, detail, retry, cancel

**Checkpoint M2:** discovery runs daily + on demand without duplicates; unauthorized sources can't be ingested; an uploaded file and a permitted URL both reach `validated`.

### Phase 3 — AI Transcription & Viral Analysis (M3)
- T16 Audio extraction + chunking stage
- T17 AI provider protocols, registry, typed settings schema + job snapshot, usage/cost tracking, quota guard, `/ai/providers`
- T18 Gemini `gemini-3.5-transcribe` adapter + timestamp normalization/merge
- T19 Scoring + candidate selection (pure functions)
- T20 Frame sampling stage
- T21 Gemini analysis adapter + OpenRouter fallback (windowed, structured output, vision on shortlist, generated titles)
- T22 Wire transcribe → analyze → select stages; `completed_no_clips` path

**Checkpoint M3:** a real 60–120 min authorized video yields valid, timestamped, scored candidates (or a justified no-clip result); cost/latency recorded.

### Phase 4 — Rendering, Drive, Notifications (M4)
- T23 Subtitle (ASS) generation from word timings
- T24 Render stage 720×1280 + output verification
- T25 Google Drive OAuth connect/callback/status + encrypted token storage
- T26 Drive resumable upload + reconciliation + verification + local cleanup
- T27 Telegram bot notifications
- T28 Workspace retention / temp cleanup; worker systemd unit with resource limits

**Checkpoint M4 (spec gate before frontend):** one full 60–120 min video goes end-to-end on the VPS: ≤ 3 clips, verified Drive IDs, no duplicates after a forced mid-upload kill, ≥ 5 GB free disk preserved.

### Phase 5 — Review API & Affiliate Backend (M5 backend half)
- T29 Clips API: list/detail/review with audit trail
- T30 Settings (validated, audited), AI cost aggregates, dashboard API
- T31 `006_affiliate_links.sql` + affiliate catalog API with link validation
- T32 Affiliate matcher stage + rematch + toggle gating + events
- T33 Clip affiliate-link review + caption/description export with disclosure

**Checkpoint:** full API contract from spec implemented; OpenAPI schema exported for the frontend; affiliate acceptance rules pass as API tests.

### Phase 6 — Telegram Mini App (M5 frontend half)
- T34 Frontend scaffold: Vite/React/TS, Telegram SDK, auth handshake, API client, routing
- T35 Dashboard + Jobs list/detail
- T36 Sources, Videos, permitted URL + chunked file upload UI
- T37 Clip preview & approve/reject
- T38 Affiliate: toggle, catalog management, suggestion review, copyable caption
- T39 Settings: providers/thresholds, Drive connection; Nginx snippet + static deploy

**Checkpoint M5:** admin can configure, monitor, preview, approve/reject, manage affiliate catalog, toggle matching and approve links entirely inside Telegram.

### Phase 7 — Reliability, Hardening, Handover (M6–M7)
- T40 Reliability suite: crash/lease recovery, upload reconciliation, retries, timeouts
- T41 Security hardening review: auth rejection, RLS/grants, SSRF, secret-log audit
- T42 Resource benchmark + per-stage metrics (5/10/20 videos/day capacity)
- T43 Docs: installation, backup & restore, operations runbook
- T44 Production rollout + 7-day pilot tracking

**Checkpoint M6/M7:** all Must-Haves, security, resource and recovery tests pass; pilot metrics table from spec tracked; system runs unattended.

## Parallelization

- Safe in parallel after T08: **T10–T11 (discovery)** ‖ **T12–T14 (ingest)**; after T17: **T18 (transcription)** ‖ **T19 (scoring, pure)** ‖ **T20 (frames)**; **T23 (subtitles)** ‖ **T25 (Drive OAuth)**.
- Frontend T34 can start once T05 + OpenAPI from T29/T30 exist, but per spec, full UI work (T35+) waits for Checkpoint M4.
- Migrations (T02–T04, T31) are strictly sequential.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| 4 GB RAM / 2 vCPU too small for 120-min media + render | High | Audio-only processing for AI; never decode full video in Python; ffmpeg `-threads 2`, render concurrency 1, `MemoryMax=1800M`; seek-based clip extraction; benchmark early (Checkpoint M4) before promising 20/day. |
| Disk (20–30 GB, 5 GB reserved) exhausted by source media | High | Admission check projected size (source + audio + 3 clips × est.) before ingest; delete source after clips verified; startup GC of orphaned workspace dirs. |
| Authorization/rights misuse (downloading YouTube content) | High | No YouTube downloader dependency; permission evidence mandatory on media registration; URL allowlist + SSRF guard; `PermanentError` on rights failure. |
| Duplicate Drive uploads after crash | High | appProperties identity + reconciliation query + persisted resumable session URI (ADR-014); dedicated kill-mid-upload test. |
| Cost estimates drift from real provider bills | Low | Prices are admin-editable and snapshotted per job; reports labeled "estimated"; compare against provider invoices during the pilot (T44). |
| AI cost/latency for 2-hour transcripts | Med | Windowed analysis, frames only for shortlist, per-job cost recorded, configurable models; stage outputs persisted so retries don't re-bill. |
| LLM output malformed/hallucinated timestamps | Med | Pydantic-validated structured output, clamp to transcript bounds, snap to segment boundaries, reject + one repair retry. |
| Supabase connectivity (IPv6-only direct host, pooler semantics) | Med | Session pooler, TLS verify, connection health in `/health/ready`, retry on transient DB errors; decide in T02. |
| Telegram webview can't play private Drive files | Low | Decided: open externally via `openLink` (ADR-029); admin must be signed in to the uploading Google account on the phone. |
| Free-tier quotas too small or cut again | High | Quota guard defers jobs to after reset (ADR-028); chunk size and models are settings; OpenRouter fallback for analysis; paid tier is a settings change; measure real capacity in T42. |
| Free-tier data used for provider training | Med | Documented trade-off; confirm creator authorization covers it or move to paid tier before the pilot. |
| Preview/free models renamed or removed | Med | Model IDs live in `ai_provider_configs`; readiness check calls the provider's model-list endpoint and flags missing models. |
| Trending chart for `ID` is mostly short videos → few 30–120 min candidates | Med | Expected; discovery also pulls top-category charts and monitored channels (more reliable long-form source); discovery summary notification reports how many were eligible. |
| Lease fencing bugs → stale writes | Med | Single repository function for all job writes (always fenced); concurrency tests with two claimers against real Postgres. |
| Dev on Windows vs prod Ubuntu (signals, paths) | Low | `pathlib` everywhere, workspace path from env, signal handling guarded per-platform, final verification on VPS. |
| Dependency drift / API changes | Med | Exact `==` pins for direct deps in `pyproject.toml`; transitive deps unlocked, so a new transitive release can break a fresh VPS install — mitigated by CI catching it first, recording `uv pip freeze` per release, and upgrading deliberately. Confirm current versions/APIs from official docs during T01 and each integration task. |

## Remaining Open Items

- Read the actual free-tier RPM/RPD for each chosen Gemini model from AI Studio (per project) and enter them as quota settings before the pilot.
- Confirm that creator authorization permits processing by free-tier AI services whose terms allow training on submitted content (see ADR-027).
