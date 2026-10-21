import { ApiError, api } from "../api/client";
import type { UploadInit, UploadOut } from "../api/types";

/** The API's chunk limit (ADR-017); Nginx caps request bodies at 10 MiB. */
export const CHUNK_BYTES = 8 * 1024 * 1024;
const DEFAULT_MAX_RETRIES = 8;

export interface UploadTransport {
  start(init: UploadInit): Promise<UploadOut>;
  status(uploadId: string): Promise<UploadOut>;
  sendChunk(uploadId: string, chunk: Blob, start: number, total: number): Promise<UploadOut>;
  complete(uploadId: string): Promise<UploadOut>;
}

export interface ResumeStore {
  get(key: string): string | null;
  set(key: string, value: string): void;
  remove(key: string): void;
}

export interface UploadOptions {
  file: Blob;
  filename: string;
  videoId: string;
  sha256: string;
  transport?: UploadTransport;
  store?: ResumeStore;
  onStarted?: (upload: UploadOut) => void;
  onProgress?: (sentBytes: number, totalBytes: number) => void;
  chunkBytes?: number;
  maxRetries?: number;
  sleep?: (ms: number) => Promise<void>;
}

/** Raised after repeated transient failures; calling `uploadFile` again with the same file resumes. */
export class UploadInterrupted extends Error {
  readonly uploadId: string;
  readonly receivedBytes: number;

  constructor(uploadId: string, receivedBytes: number, cause: unknown) {
    super("Upload paused: the connection keeps failing. Resume when you are back online.", { cause });
    this.name = "UploadInterrupted";
    this.uploadId = uploadId;
    this.receivedBytes = receivedBytes;
  }
}

export const apiTransport: UploadTransport = {
  start: (init) => api.post<UploadOut>("/uploads", init),
  status: (uploadId) => api.get<UploadOut>(`/uploads/${uploadId}`),
  sendChunk: (uploadId, chunk, start, total) =>
    api.request<UploadOut>("PATCH", `/uploads/${uploadId}`, {
      rawBody: chunk,
      headers: { "Content-Range": `bytes ${start}-${start + chunk.size - 1}/${total}` },
    }),
  complete: (uploadId) => api.post<UploadOut>(`/uploads/${uploadId}/complete`),
};

export const localResumeStore: ResumeStore = {
  get: (key) => {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set: (key, value) => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Without storage the upload still works; it just can't resume after the app is closed.
    }
  },
  remove: (key) => {
    try {
      window.localStorage.removeItem(key);
    } catch {
      // See set().
    }
  },
};

function isTransient(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.status >= 500 || error.status === 429;
  }
  // fetch rejects with TypeError when the network is unreachable.
  return error instanceof TypeError;
}

function backoffMs(failures: number): number {
  return Math.min(30_000, 1_000 * 2 ** (failures - 1));
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function resumable(transport: UploadTransport, store: ResumeStore, key: string): Promise<UploadOut | null> {
  const uploadId = store.get(key);
  if (uploadId === null) {
    return null;
  }
  try {
    const existing = await transport.status(uploadId);
    if (existing.state === "receiving") {
      return existing;
    }
  } catch (error) {
    if (isTransient(error)) {
      throw error;
    }
  }
  store.remove(key);
  return null;
}

export async function uploadFile(options: UploadOptions): Promise<UploadOut> {
  const {
    file,
    transport = apiTransport,
    store = localResumeStore,
    chunkBytes = CHUNK_BYTES,
    maxRetries = DEFAULT_MAX_RETRIES,
    sleep = wait,
  } = options;
  const total = file.size;
  const key = `upload:${options.videoId}:${options.sha256}:${total}`;

  let upload =
    (await resumable(transport, store, key)) ??
    (await transport.start({
      video_id: options.videoId,
      filename: options.filename,
      size_bytes: total,
      sha256: options.sha256,
    }));
  store.set(key, upload.id);
  options.onStarted?.(upload);

  let offset = upload.received_bytes;
  let failures = 0;
  options.onProgress?.(offset, total);
  while (offset < total) {
    const chunk = file.slice(offset, Math.min(offset + chunkBytes, total));
    try {
      upload = await transport.sendChunk(upload.id, chunk, offset, total);
      offset = upload.received_bytes;
      failures = 0;
      options.onProgress?.(offset, total);
      continue;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        upload = await transport.status(upload.id);
        if (upload.state !== "receiving") {
          store.remove(key);
          throw error;
        }
        offset = upload.received_bytes;
        continue;
      }
      if (!isTransient(error)) {
        throw error;
      }
      failures += 1;
      if (failures > maxRetries) {
        throw new UploadInterrupted(upload.id, offset, error);
      }
    }
    await sleep(backoffMs(failures));
    // A chunk can be stored even though its response was lost, so ask the server where to continue.
    try {
      offset = (await transport.status(upload.id)).received_bytes;
    } catch (error) {
      if (!isTransient(error)) {
        throw error;
      }
    }
  }

  const completed = await transport.complete(upload.id);
  store.remove(key);
  return completed;
}
