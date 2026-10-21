import { describe, expect, it } from "vitest";
import type { JobEventOut } from "../api/types";
import { buildStageTimeline } from "./timeline";

function event(id: number, eventType: string, stage: string | null, at: string, extra: Partial<JobEventOut> = {}): JobEventOut {
  return {
    id,
    event_type: eventType,
    stage,
    status: null,
    attempt: null,
    message: null,
    details: {},
    created_at: at,
    ...extra,
  };
}

describe("buildStageTimeline", () => {
  it("groups consecutive events by stage and measures each stage until the next one starts", () => {
    const events = [
      event(1, "created", "queued", "2026-10-21T10:00:00Z"),
      event(2, "claimed", "ingest", "2026-10-21T10:00:05Z"),
      event(3, "stage_changed", "audio", "2026-10-21T10:01:05Z"),
      event(4, "retry_scheduled", "audio", "2026-10-21T10:01:35Z", { message: "timeout" }),
      event(5, "stage_changed", "transcribe", "2026-10-21T10:03:05Z"),
      event(6, "completed", "completed", "2026-10-21T10:10:05Z"),
    ];

    const timeline = buildStageTimeline(events, "2026-10-21T10:10:05Z");

    expect(timeline.map((s) => [s.stage, s.durationMs])).toEqual([
      ["queued", 5_000],
      ["ingest", 60_000],
      ["audio", 120_000],
      ["transcribe", 420_000],
      ["completed", 0],
    ]);
    expect(timeline[2]?.events.map((e) => e.event_type)).toEqual(["stage_changed", "retry_scheduled"]);
  });

  it("leaves the current stage of an unfinished job open", () => {
    const events = [
      event(1, "created", "queued", "2026-10-21T10:00:00Z"),
      event(2, "claimed", "ingest", "2026-10-21T10:00:05Z"),
    ];

    const timeline = buildStageTimeline(events, null);

    expect(timeline[1]).toMatchObject({ stage: "ingest", durationMs: null });
  });

  it("attaches stageless events to the current stage", () => {
    const events = [
      event(1, "created", "queued", "2026-10-21T10:00:00Z"),
      event(2, "cancel_requested", null, "2026-10-21T10:00:01Z"),
    ];

    expect(buildStageTimeline(events, null)).toHaveLength(1);
  });
});
