// Hand-typed from openapi.json (regenerate with `python backend/scripts/export_openapi.py`).
// Money fields are decimal strings so amounts are never rounded through floats.

export type Uuid = string;
export type IsoDateTime = string;

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface SessionResponse {
  access_token: string;
  token_type: "bearer";
  expires_at: IsoDateTime;
}

export interface CostOut {
  currency: string;
  estimated: string;
}

export interface DayCost extends CostOut {
  day: string;
}

export interface ModelCost extends CostOut {
  provider: string;
  model: string;
}

export interface StageCost extends CostOut {
  stage: string | null;
}

export interface CostWindow {
  totals: CostOut[];
  by_day: DayCost[];
  by_model: ModelCost[];
  by_stage: StageCost[];
}

export interface DashboardOut {
  jobs_by_status: Record<string, number>;
  clips_awaiting_review: number;
  last_discovery_run_at: IsoDateTime | null;
  disk_free_bytes: number | null;
  costs: {
    last_7_days: CostWindow;
    last_30_days: CostWindow;
    average_per_completed_job: CostOut[];
    completed_jobs: number;
  };
}

export const JOB_STATUSES = [
  "awaiting_source",
  "queued",
  "running",
  "retry_scheduled",
  "completed",
  "completed_no_clips",
  "failed",
  "cancelled",
] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export interface JobOut {
  id: Uuid;
  video_id: Uuid;
  media_asset_id: Uuid | null;
  status: JobStatus;
  stage: string;
  attempts: number;
  max_attempts: number;
  cancel_requested: boolean;
  last_error: string | null;
  next_attempt_at: IsoDateTime;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface JobEventOut {
  id: number;
  event_type: string;
  stage: string | null;
  status: string | null;
  attempt: number | null;
  message: string | null;
  details: Record<string, unknown>;
  created_at: IsoDateTime;
}

export interface JobDetail extends JobOut {
  events: JobEventOut[];
  costs: CostOut[];
  costs_by_stage: StageCost[];
}

export type SourceKind = "trending" | "channel";

export interface SourceOut {
  id: Uuid;
  kind: SourceKind;
  name: string;
  channel_id: string | null;
  region_code: string | null;
  enabled: boolean;
  last_run_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface SourceCreate {
  kind: SourceKind;
  name: string;
  channel_id?: string | null;
  region_code?: string | null;
}

export interface SourceUpdate {
  name?: string;
  region_code?: string | null;
  enabled?: boolean;
}

export type Eligibility = "pending" | "eligible" | "ineligible";
export type RightsStatus = "unverified" | "authorized" | "rejected";
export type SourceStatus = "awaiting_source" | "ready";

export interface VideoOut {
  id: Uuid;
  youtube_video_id: string;
  title: string;
  channel_id: string;
  channel_title: string | null;
  category_id: string | null;
  duration_seconds: number | null;
  published_at: IsoDateTime | null;
  view_count: number | null;
  thumbnail_url: string | null;
  eligibility: Eligibility;
  rights_status: RightsStatus;
  source_status: SourceStatus;
  discovered_at: IsoDateTime;
}

export type PermissionType = "own_content" | "creator_permission" | "license";

export interface PermissionEvidence {
  type: PermissionType;
  statement: string;
  reference?: string | null;
}

export interface MediaOut {
  id: Uuid;
  video_id: Uuid;
  kind: string;
  source_url: string | null;
  state: string;
  created_at: IsoDateTime;
}

export interface UploadInit {
  video_id: Uuid;
  filename: string;
  size_bytes: number;
  sha256: string;
  permission: PermissionEvidence;
}

export interface UploadOut {
  id: Uuid;
  video_id: Uuid;
  filename: string | null;
  state: string;
  size_bytes: number | null;
  received_bytes: number;
  failure_reason: string | null;
  created_at: IsoDateTime;
}

export type ReviewStatus = "pending_review" | "approved" | "rejected";
export type ReviewDecision = "approved" | "rejected";

export interface ClipOut {
  id: Uuid;
  job_id: Uuid;
  video_id: Uuid;
  video_title: string;
  start_ms: number;
  end_ms: number;
  review_status: ReviewStatus;
  title: string | null;
  title_override: string | null;
  effective_title: string | null;
  total_score: number;
  rank: number | null;
  drive_file_id: string | null;
  drive_web_view_link: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface ClipScores {
  hook: number;
  emotion: number;
  clarity: number;
  relevance: number;
  visual: number;
  completeness: number;
  total: number;
}

export interface ClipArtifactOut {
  id: Uuid;
  kind: string;
  size_bytes: number | null;
  upload_state: string;
  drive_file_id: string | null;
  created_at: IsoDateTime;
}

export interface ClipReviewOut {
  id: Uuid;
  admin_user_id: Uuid;
  decision: ReviewDecision;
  note: string | null;
  title_override: string | null;
  created_at: IsoDateTime;
}

export interface CaptionLinkOut {
  name: string;
  merchant: string;
  url: string;
  is_primary: boolean;
}

export interface CaptionOut {
  text: string;
  links: CaptionLinkOut[];
  disclosure: string | null;
}

export interface ClipDetail extends ClipOut {
  hook_line: string | null;
  explanation: string | null;
  transcript_excerpt: string | null;
  scores: ClipScores;
  artifacts: ClipArtifactOut[];
  reviews: ClipReviewOut[];
  job_costs: CostOut[];
  caption: CaptionOut;
}

export interface ClipReviewIn {
  decision: ReviewDecision;
  note?: string | null;
  title_override?: string | null;
}

export interface ProductOut {
  id: Uuid;
  name: string;
  merchant: string;
  affiliate_url: string;
  destination_domain: string;
  category: string | null;
  tags: string[];
  language_code: string | null;
  region_code: string | null;
  active: boolean;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface ProductCreate {
  name: string;
  merchant: string;
  affiliate_url: string;
  destination_domain: string;
  category?: string | null;
  tags?: string[];
  language_code?: string | null;
  region_code?: string | null;
  active?: boolean;
}

export type ProductUpdate = Partial<ProductCreate>;

export type AffiliateLinkStatus = "suggested" | "approved" | "rejected";

export interface AffiliateLinkOut {
  id: Uuid;
  clip_id: Uuid;
  product: ProductOut;
  relevance_score: number;
  match_reason: string | null;
  status: AffiliateLinkStatus;
  is_primary: boolean;
  reviewed_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface AffiliateLinkList {
  enabled: boolean;
  items: AffiliateLinkOut[];
}

export interface DiscoverySettings {
  region_code: string;
  top_categories: number;
  chart_pages: number;
  per_category_results: number;
  max_new_eligible_per_run: number;
  excluded_category_ids: string[];
}

export interface ModelSelection {
  transcription: string;
  analysis: string;
  rerank: string;
  analysis_fallback: string | null;
  embedding: string;
}

export interface AdminSettings {
  min_score: number;
  clip_min_s: number;
  clip_max_s: number;
  max_clips: number;
  max_overlap: number;
  audio_chunk_minutes: number;
  reserved_disk_bytes: number;
  daily_job_cap: number | null;
  permitted_download_domains: string[];
  affiliate_links_enabled: boolean;
  affiliate_min_relevance: number;
  affiliate_merchant_domains: string[];
  affiliate_disclosure_text: string;
  telegram_notifications_enabled: boolean;
  discovery: DiscoverySettings;
  models: ModelSelection;
}

export interface ProviderPatch {
  id: Uuid;
  enabled?: boolean;
  rpm_limit?: number | null;
  rpd_limit?: number | null;
  price_input_per_mtok?: string;
  price_output_per_mtok?: string;
  price_per_audio_minute?: string;
  currency?: string;
}

export type SettingsPatch = Partial<
  Omit<AdminSettings, "discovery" | "models"> & {
    discovery: Partial<DiscoverySettings>;
    models: Partial<ModelSelection>;
    providers: ProviderPatch[];
  }
>;

export interface ProviderOut {
  id: Uuid;
  provider: string;
  model: string;
  capability: string;
  enabled: boolean;
  selected_for: string[];
  secret_configured: boolean;
  prices: {
    input_per_mtok: string;
    output_per_mtok: string;
    per_audio_minute: string;
    currency: string;
  };
  quota: {
    rpm_limit: number | null;
    rpd_limit: number | null;
    reset: string;
    used_today: number;
  };
}

export type DriveConnection = "disconnected" | "connected" | "expired" | "unavailable";

export interface DriveStatus {
  status: DriveConnection;
  scopes: string[];
  account_email: string | null;
  target_folder_name: string;
  target_folder_id: string | null;
}
