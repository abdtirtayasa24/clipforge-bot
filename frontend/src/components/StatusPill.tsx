type Tone = "success" | "warning" | "danger" | "info" | "neutral";

const TONES: Record<string, Tone> = {
  completed: "success",
  approved: "success",
  eligible: "success",
  authorized: "success",
  ready: "success",
  connected: "success",
  running: "info",
  queued: "warning",
  retry_scheduled: "warning",
  awaiting_source: "warning",
  pending_review: "warning",
  pending: "warning",
  unverified: "warning",
  suggested: "warning",
  failed: "danger",
  rejected: "danger",
  ineligible: "danger",
  inactive: "neutral",
};

/** A coloured status label; the tone comes from the status value itself. */
export function StatusPill({ status, label }: { status: string; label?: string }) {
  return <span className={`badge ${TONES[status] ?? "neutral"}`}>{label ?? status.replaceAll("_", " ")}</span>;
}
