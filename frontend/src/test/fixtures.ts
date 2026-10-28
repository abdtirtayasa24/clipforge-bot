import type {
  AdminSettings,
  AffiliateLinkOut,
  ClipDetail,
  ClipOut,
  DashboardOut,
  JobDetail,
  JobListItem,
  JobOut,
  ProductOut,
  VideoOut,
} from "../api/types";

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

export function jobListItemFixture(overrides: Partial<JobListItem> = {}): JobListItem {
  return { ...jobFixture(), video_title: "Masa depan AI", ...overrides };
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

export function clipFixture(overrides: Partial<ClipOut> = {}): ClipOut {
  return {
    id: "55555555-5555-5555-5555-555555555555",
    job_id: "11111111-1111-1111-1111-111111111111",
    video_id: "44444444-4444-4444-4444-444444444444",
    video_title: "Long podcast episode",
    start_ms: 754_000,
    end_ms: 799_500,
    review_status: "pending_review",
    title: "Why sleep beats caffeine",
    title_override: null,
    effective_title: "Why sleep beats caffeine",
    total_score: 82.5,
    rank: 1,
    drive_file_id: "drive-1",
    drive_web_view_link: "https://drive.google.com/file/d/drive-1/view",
    created_at: "2026-10-21T11:00:00Z",
    updated_at: "2026-10-21T11:00:00Z",
    ...overrides,
  };
}

export function clipDetailFixture(overrides: Partial<ClipDetail> = {}): ClipDetail {
  return {
    ...clipFixture(),
    hook_line: "You are drinking coffee wrong.",
    explanation: "Strong hook and a complete argument.",
    transcript_excerpt: "So the thing about caffeine is...",
    scores: { hook: 90, emotion: 70, clarity: 85, relevance: 80, visual: 60, completeness: 88, total: 82.5 },
    artifacts: [],
    reviews: [],
    job_costs: [{ currency: "USD", estimated: "0.012" }],
    caption: { text: "Why sleep beats caffeine\n\nYou are drinking coffee wrong.", links: [], disclosure: null },
    ...overrides,
  };
}

export function productFixture(overrides: Partial<ProductOut> = {}): ProductOut {
  return {
    id: "66666666-6666-6666-6666-666666666666",
    name: "Sleep mask",
    merchant: "Tokopedia",
    affiliate_url: "https://tokopedia.link/abc?aff=1",
    destination_domain: "tokopedia.link",
    category: "health",
    tags: ["sleep"],
    language_code: "id",
    region_code: "ID",
    active: true,
    created_at: "2026-10-21T09:00:00Z",
    updated_at: "2026-10-21T09:00:00Z",
    ...overrides,
  };
}

export function linkFixture(overrides: Partial<AffiliateLinkOut> = {}): AffiliateLinkOut {
  return {
    id: "77777777-7777-7777-7777-777777777777",
    clip_id: "55555555-5555-5555-5555-555555555555",
    product: productFixture(),
    relevance_score: 0.83,
    match_reason: "Clip discusses sleep quality",
    status: "suggested",
    is_primary: false,
    reviewed_at: null,
    created_at: "2026-10-21T11:05:00Z",
    ...overrides,
  };
}

export function settingsFixture(overrides: Partial<AdminSettings> = {}): AdminSettings {
  return {
    min_score: 75,
    clip_min_s: 60,
    clip_max_s: 90,
    max_clips: 3,
    max_overlap: 0.2,
    audio_chunk_minutes: 20,
    reserved_disk_bytes: 5368709120,
    daily_job_cap: null,
    permitted_download_domains: ["cdn.example.com"],
    title_overlay_enabled: true,
    subject_reframing_enabled: false,
    affiliate_links_enabled: false,
    affiliate_min_relevance: 0.7,
    affiliate_merchant_domains: [],
    affiliate_disclosure_text: "Affiliate link — I may earn a commission from qualifying purchases.",
    telegram_notifications_enabled: true,
    discovery: {
      region_code: "ID",
      top_categories: 5,
      chart_pages: 4,
      per_category_results: 50,
      max_new_eligible_per_run: 20,
      excluded_category_ids: [],
      auto_enabled: true,
      auto_hour: 3,
    },
    models: {
      transcription: "gemini-3.5-transcribe",
      analysis: "gemini-3.5-flash-lite",
      rerank: "gemini-3-flash-preview",
      analysis_fallback: null,
      embedding: "gemini-embedding-2",
    },
    ...overrides,
  };
}
