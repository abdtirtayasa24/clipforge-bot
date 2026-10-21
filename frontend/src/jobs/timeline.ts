import type { JobEventOut } from "../api/types";

export interface StageSegment {
  stage: string;
  startedAt: string;
  /** Null while the job is still in this stage. */
  durationMs: number | null;
  events: JobEventOut[];
}

/**
 * Splits the job's event log into consecutive stage runs. A stage lasts until the first event of
 * the next stage; the last stage ends when the job finished, or is still open.
 */
export function buildStageTimeline(events: JobEventOut[], finishedAt: string | null): StageSegment[] {
  const segments: StageSegment[] = [];
  for (const event of events) {
    const current = segments.at(-1);
    if (current && (event.stage === null || event.stage === current.stage)) {
      current.events.push(event);
      continue;
    }
    if (current) {
      current.durationMs = Date.parse(event.created_at) - Date.parse(current.startedAt);
    }
    segments.push({
      stage: event.stage ?? "unknown",
      startedAt: event.created_at,
      durationMs: null,
      events: [event],
    });
  }
  const last = segments.at(-1);
  if (last && finishedAt !== null) {
    last.durationMs = Math.max(0, Date.parse(finishedAt) - Date.parse(last.startedAt));
  }
  return segments;
}
