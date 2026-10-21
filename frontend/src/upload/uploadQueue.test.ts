import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { UploadOut } from "../api/types";
import { mockApi } from "../test/fetchMock";
import { type ResumeStore, UploadInterrupted, uploadFile } from "./chunkedUpload";
import { SLOT_POLL_MS, UploadQueue, summarizeUploads } from "./uploadQueue";

vi.mock("./sha256", () => ({ sha256InWorker: vi.fn(async () => "f".repeat(64)) }));
vi.mock("./chunkedUpload", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./chunkedUpload")>()),
  uploadFile: vi.fn(),
}));

function memoryStore(initial: Record<string, string> = {}): ResumeStore {
  const data = new Map(Object.entries(initial));
  return {
    get: (key) => data.get(key) ?? null,
    set: (key, value) => {
      data.set(key, value);
    },
    remove: (key) => {
      data.delete(key);
    },
  };
}

function serverUpload(id: string, overrides: Partial<UploadOut> = {}): UploadOut {
  return {
    id,
    video_id: "v1",
    filename: "talk.mp4",
    state: "receiving",
    size_bytes: 8,
    received_bytes: 0,
    failure_reason: null,
    created_at: "2026-10-22T10:00:00Z",
    ...overrides,
  };
}

function file(name: string): File {
  return new File(["01234567"], name, { type: "video/mp4" });
}

/** uploadFile calls that finish only when the test says so. */
function controlledUploads() {
  const finishers: (() => void)[] = [];
  vi.mocked(uploadFile).mockImplementation(
    (options) =>
      new Promise((resolve) => {
        options.onStarted?.(serverUpload(`up-${options.filename}`));
        finishers.push(() => resolve(serverUpload(`up-${options.filename}`, { state: "ready" })));
      }),
  );
  return { finishNext: () => finishers.shift()?.() };
}

beforeEach(() => {
  vi.mocked(uploadFile).mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("UploadQueue", () => {
  it("uploads one file at a time in the order they were added", async () => {
    mockApi({ "GET /api/v1/uploads/active": { body: null } });
    const uploads = controlledUploads();
    const queue = new UploadQueue(memoryStore());

    queue.add("v1", "First talk", file("a.mp4"));
    queue.add("v2", "Second talk", file("b.mp4"));

    await vi.waitFor(() => expect(queue.snapshot().map((item) => item.state)).toEqual(["uploading", "queued"]));
    expect(uploadFile).toHaveBeenCalledTimes(1);
    uploads.finishNext();
    await vi.waitFor(() => expect(queue.snapshot().map((item) => item.state)).toEqual(["complete", "uploading"]));
    expect(vi.mocked(uploadFile).mock.calls[1]?.[0]).toMatchObject({ videoId: "v2", filename: "b.mp4" });
    expect(queue.snapshot()[0]?.uploadId).toBe("up-a.mp4");
  });

  it("waits while another upload holds the server's slot, then starts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let active: UploadOut | null = serverUpload("someone-else");
    mockApi({ "GET /api/v1/uploads/active": () => ({ body: active }) });
    controlledUploads();
    const queue = new UploadQueue(memoryStore());

    queue.add("v1", "Talk", file("a.mp4"));
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(queue.snapshot()[0]?.state).toBe("queued");

    active = null;
    await vi.advanceTimersByTimeAsync(SLOT_POLL_MS);

    await vi.waitFor(() => expect(queue.snapshot()[0]?.state).toBe("uploading"));
  });

  it("goes back to waiting when the server refuses a second upload", async () => {
    mockApi({ "GET /api/v1/uploads/active": { body: null } });
    vi.mocked(uploadFile).mockRejectedValueOnce(
      new ApiError(409, "conflict", "another upload is in progress", { upload_id: "u9", video_id: "v9" }),
    );
    const queue = new UploadQueue(memoryStore());

    queue.add("v1", "Talk", file("a.mp4"));

    await vi.waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(queue.snapshot()[0]).toMatchObject({ state: "queued", error: null }));
  });

  it("pauses on repeated network failures and resumes on request", async () => {
    mockApi({ "GET /api/v1/uploads/active": { body: null } });
    vi.mocked(uploadFile).mockRejectedValueOnce(new UploadInterrupted("u1", 4, new TypeError("offline")));
    const queue = new UploadQueue(memoryStore());
    queue.add("v1", "Talk", file("a.mp4"));
    await vi.waitFor(() => expect(queue.snapshot()[0]?.state).toBe("interrupted"));
    expect(queue.snapshot()[0]?.error).toMatch(/Upload paused/);
    vi.mocked(uploadFile).mockResolvedValueOnce(serverUpload("u1", { state: "ready" }));

    queue.retry(queue.snapshot()[0]?.id ?? "");

    await vi.waitFor(() => expect(queue.snapshot()[0]?.state).toBe("complete"));
  });

  it("brings in-flight uploads back as waiting for their file after a reload", async () => {
    const store = memoryStore();
    mockApi({ "GET /api/v1/uploads/active": { body: null } });
    controlledUploads();
    const before = new UploadQueue(store);
    before.add("v1", "Talk", file("a.mp4"));
    before.add("v2", "Other", file("b.mp4"));
    await vi.waitFor(() => expect(before.snapshot()[0]?.uploadId).toBe("up-a.mp4"));

    const after = new UploadQueue(store);

    expect(after.snapshot().map((item) => [item.videoTitle, item.state, item.uploadId])).toEqual([
      ["Talk", "needs_file", "up-a.mp4"],
      ["Other", "needs_file", null],
    ]);
  });

  it("resumes the upload holding the slot before files that have not started", async () => {
    const paused = { id: "p1", videoId: "v1", videoTitle: "Talk", filename: "a.mp4", size: 8, done: 0 };
    const store = memoryStore({
      "uploads:queue": JSON.stringify([{ ...paused, state: "uploading", uploadId: "up-a.mp4", error: null }]),
    });
    mockApi({ "GET /api/v1/uploads/active": { body: serverUpload("up-a.mp4") } });
    controlledUploads();
    const after = new UploadQueue(store);
    after.add("v2", "Other", file("b.mp4"));

    after.attachFile("p1", file("a.mp4"));

    await vi.waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(1));
    expect(vi.mocked(uploadFile).mock.calls[0]?.[0]).toMatchObject({ videoId: "v1" });
  });

  it("cancels the server upload when an entry is removed", async () => {
    const calls = mockApi({
      "GET /api/v1/uploads/active": { body: null },
      "DELETE /api/v1/uploads/up-a.mp4": { body: serverUpload("up-a.mp4", { state: "failed" }) },
    });
    controlledUploads();
    const queue = new UploadQueue(memoryStore());
    queue.add("v1", "Talk", file("a.mp4"));
    await vi.waitFor(() => expect(queue.snapshot()[0]?.uploadId).toBe("up-a.mp4"));

    await queue.remove(queue.snapshot()[0]?.id ?? "");

    expect(calls.some((call) => call.method === "DELETE")).toBe(true);
    expect(queue.snapshot()).toEqual([]);
  });

  it("summarizes the queue for the Sources page", () => {
    const queue = new UploadQueue(
      memoryStore({
        "uploads:queue": JSON.stringify([
          { id: "1", state: "complete" },
          { id: "2", state: "failed" },
          { id: "3", state: "uploading" },
        ]),
      }),
    );

    expect(summarizeUploads(queue.snapshot())).toBe("1 paused · 1 failed");
    expect(summarizeUploads([])).toBe("No uploads");
  });
});
