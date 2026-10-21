import type { DashboardOut, JobDetail, JobOut, VideoOut } from "../api/types";

export function jobFixture(overrides: Partial<JobOut> = {}): JobOut {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    video_id: "22222222-2222-2222-2222-222222222222",
    media_asset_id: "33333333-3333-3333-3333-333333333333",
    status: "running",
    stage: "transcribe",
    attempts: 1,
    max_attempts: 5,
    cancel_requested: false,
    last_error: null,
    next_attempt_at: "2026-10-21T10:00:00Z",
    started_at: "2026-10-21T10:00:05Z",
    finished_at: null,
    created_at: "2026-10-21T10:00:00Z",
    updated_at: "2026-10-21T10:03:05Z",
    ...overrides,
  };
}

export function jobDetailFixture(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    ...jobFixture(),
    events: [
      {
        id: 1,
        event_type: "created",
        stage: "queued",
        status: "queued",
        attempt: 0,
        message: null,
        details: {},
        created_at: "2026-10-21T10:00:00Z",
      },
      {
        id: 2,
        event_type: "stage_changed",
        stage: "transcribe",
        status: "running",
        attempt: 1,
        message: null,
        details: {},
        created_at: "2026-10-21T10:01:00Z",
      },
    ],
    costs: [{ currency: "USD", estimated: "0.0015" }],
    costs_by_stage: [
      { stage: "analyze", currency: "USD", estimated: "0.0005" },
      { stage: "transcribe", currency: "USD", estimated: "0.001" },
    ],
    ...overrides,
  };
}

export function dashboardFixture(overrides: Partial<DashboardOut> = {}): DashboardOut {
  const costWindow = {
    totals: [{ currency: "USD", estimated: "0.42" }],
    by_day: [{ day: "2026-10-20", currency: "USD", estimated: "0.42" }],
    by_model: [{ provider: "gemini", model: "gemini-3.5-flash-lite", currency: "USD", estimated: "0.40" }],
    by_stage: [{ stage: "analyze", currency: "USD", estimated: "0.30" }],
  };
  return {
    jobs_by_status: { running: 1, completed: 4, failed: 1 },
    clips_awaiting_review: 3,
    last_discovery_run_at: "2026-10-21T06:00:00Z",
    disk_free_bytes: 12 * 1024 ** 3,
    costs: {
      last_7_days: costWindow,
      last_30_days: { ...costWindow, totals: [{ currency: "USD", estimated: "1.75" }] },
      average_per_completed_job: [{ currency: "USD", estimated: "0.105" }],
      completed_jobs: 4,
    },
    ...overrides,
  };
}

export function videoFixture(overrides: Partial<VideoOut> = {}): VideoOut {
  return {
    id: "44444444-4444-4444-4444-444444444444",
    youtube_video_id: "abcdefghijk",
    title: "Long podcast episode",
    channel_id: "UC1234567890123456789012",
    channel_title: "Podcast ID",
    category_id: "22",
    duration_seconds: 5400,
    published_at: "2026-10-20T10:00:00Z",
    view_count: 120000,
    thumbnail_url: null,
    eligibility: "eligible",
    rights_status: "unverified",
    source_status: "awaiting_source",
    discovered_at: "2026-10-21T06:00:00Z",
    ...overrides,
  };
}
