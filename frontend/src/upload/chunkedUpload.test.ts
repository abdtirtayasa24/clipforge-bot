import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { UploadOut } from "../api/types";
import { type ResumeStore, UploadInterrupted, type UploadTransport, uploadFile } from "./chunkedUpload";

const SHA = "a".repeat(64);

function upload(received: number, state = "receiving", size = 10): UploadOut {
  return {
    id: "up-1",
    video_id: "vid-1",
    filename: "talk.mp4",
    state,
    size_bytes: size,
    received_bytes: received,
    failure_reason: null,
    created_at: "2026-10-21T10:00:00Z",
  };
}

/** A fake server holding the received offset, with scripted failures per chunk start. */
function fakeServer(failures: Record<number, unknown[]> = {}) {
  let received = 0;
  const sent: [number, number][] = [];
  const transport: UploadTransport = {
    start: vi.fn(async () => upload(received)),
    status: vi.fn(async () => upload(received)),
    sendChunk: vi.fn(async (_id: string, chunk: Blob, start: number, total: number) => {
      const queued = failures[start];
      if (queued && queued.length > 0) {
        throw queued.shift();
      }
      if (start !== received) {
        throw new ApiError(409, "conflict", "chunk does not start at the received offset", { received_bytes: received });
      }
      sent.push([start, start + chunk.size]);
      received = start + chunk.size;
      return upload(received, "receiving", total);
    }),
    complete: vi.fn(async () => upload(received, "ready")),
  };
  return {
    transport,
    sent,
    setReceived: (value: number) => {
      received = value;
    },
  };
}

function memoryStore(initial: Record<string, string> = {}): ResumeStore & { data: Map<string, string> } {
  const data = new Map(Object.entries(initial));
  return {
    data,
    get: (key) => data.get(key) ?? null,
    set: (key, value) => {
      data.set(key, value);
    },
    remove: (key) => {
      data.delete(key);
    },
  };
}

function options(transport: UploadTransport, store: ResumeStore = memoryStore()) {
  return {
    file: new Blob(["0123456789"]),
    filename: "talk.mp4",
    videoId: "vid-1",
    sha256: SHA,
    transport,
    store,
    chunkBytes: 4,
    sleep: async () => undefined,
  };
}

describe("uploadFile", () => {
  it("sends bounded chunks in order, reports progress and completes", async () => {
    const server = fakeServer();
    const onProgress = vi.fn();
    const onStarted = vi.fn();
    const store = memoryStore();

    const result = await uploadFile({ ...options(server.transport, store), onProgress, onStarted });

    expect(result.state).toBe("ready");
    expect(onStarted).toHaveBeenCalledWith(expect.objectContaining({ id: "up-1", received_bytes: 0 }));
    expect(server.sent).toEqual([
      [0, 4],
      [4, 8],
      [8, 10],
    ]);
    expect(onProgress).toHaveBeenLastCalledWith(10, 10);
    expect(server.transport.start).toHaveBeenCalledWith({
      video_id: "vid-1",
      filename: "talk.mp4",
      size_bytes: 10,
      sha256: SHA,
    });
    expect(store.data.size).toBe(0);
  });

  it("resumes from the server's offset after a network drop", async () => {
    const server = fakeServer({ 4: [new TypeError("Failed to fetch"), new TypeError("Failed to fetch")] });

    const result = await uploadFile(options(server.transport));

    expect(result.state).toBe("ready");
    expect(server.sent).toEqual([
      [0, 4],
      [4, 8],
      [8, 10],
    ]);
    expect(server.transport.status).toHaveBeenCalled();
  });

  it("realigns to the received offset when a chunk was stored but its response was lost", async () => {
    const server = fakeServer();
    const lost = new TypeError("Failed to fetch");
    vi.mocked(server.transport.sendChunk).mockImplementationOnce(async () => {
      server.setReceived(4);
      throw lost;
    });

    await uploadFile(options(server.transport));

    expect(server.sent).toEqual([
      [4, 8],
      [8, 10],
    ]);
  });

  it("stops with a resumable error after repeated failures and resumes the same upload later", async () => {
    const drops = Array.from({ length: 10 }, () => new TypeError("Failed to fetch"));
    const server = fakeServer({ 4: drops });
    const store = memoryStore();

    const error = await uploadFile({ ...options(server.transport, store), maxRetries: 3 }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(UploadInterrupted);
    expect([...store.data.values()]).toEqual(["up-1"]);

    drops.length = 0;
    const result = await uploadFile(options(server.transport, store));

    expect(result.state).toBe("ready");
    expect(server.transport.start).toHaveBeenCalledOnce();
    expect(server.sent.at(-1)).toEqual([8, 10]);
  });

  it("starts a new upload when the remembered one is no longer receiving", async () => {
    const server = fakeServer();
    vi.mocked(server.transport.status).mockResolvedValueOnce(upload(4, "failed"));
    const store = memoryStore({ [`upload:vid-1:${SHA}:10`]: "old-upload" });

    await uploadFile(options(server.transport, store));

    expect(server.transport.status).toHaveBeenCalledWith("old-upload");
    expect(server.transport.start).toHaveBeenCalledOnce();
  });

  it("does not retry client errors", async () => {
    const server = fakeServer({ 0: [new ApiError(400, "bad_request", "Content-Range is out of bounds")] });

    await expect(uploadFile(options(server.transport))).rejects.toThrow("Content-Range is out of bounds");
    expect(server.transport.sendChunk).toHaveBeenCalledOnce();
  });
});
