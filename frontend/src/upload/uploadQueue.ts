import { useSyncExternalStore } from "react";
import { ApiError } from "../api/client";
import { cancelUpload, fetchActiveUpload } from "../api/uploads";
import { type ResumeStore, UploadInterrupted, localResumeStore, uploadFile } from "./chunkedUpload";
import { sha256InWorker } from "./sha256";

export type UploadItemState =
  | "queued"
  | "hashing"
  | "uploading"
  | "interrupted"
  | "needs_file"
  | "failed"
  | "complete";

export interface UploadItem {
  id: string;
  videoId: string;
  videoTitle: string;
  filename: string;
  size: number;
  state: UploadItemState;
  done: number;
  uploadId: string | null;
  error: string | null;
}

const STORAGE_KEY = "uploads:queue";
/** How often a waiting queue checks whether the server's single upload slot is free again. */
export const SLOT_POLL_MS = 15_000;
/** States that lose their file when the Mini App reloads. */
const IN_FLIGHT: UploadItemState[] = ["queued", "hashing", "uploading", "interrupted"];

export function isOpen(item: UploadItem): boolean {
  return item.state !== "complete" && item.state !== "failed";
}

function slotTaken(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    typeof error.details === "object" &&
    error.details !== null &&
    "upload_id" in error.details
  );
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

/** Files can't survive a reload, so anything still in flight comes back waiting for its file. */
function restore(store: ResumeStore): UploadItem[] {
  try {
    const saved: unknown = JSON.parse(store.get(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(saved)) {
      return [];
    }
    return (saved as UploadItem[]).map((item) =>
      IN_FLIGHT.includes(item.state) ? { ...item, state: "needs_file", done: 0 } : item,
    );
  } catch {
    return [];
  }
}

/**
 * Uploads files one at a time, in the order they were added (ADR-032). It lives outside React so
 * an upload keeps running while the admin moves between pages.
 */
export class UploadQueue {
  private items: UploadItem[];
  private readonly files = new Map<string, File>();
  private readonly hashes = new Map<string, string>();
  private readonly listeners = new Set<() => void>();
  private readonly completedListeners = new Set<() => void>();
  private running = false;
  /** A file added, attached or removed during a run may unblock the queue before the next poll. */
  private changedWhileRunning = false;
  private waitTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly store: ResumeStore;

  constructor(store: ResumeStore = localResumeStore) {
    this.store = store;
    this.items = restore(store);
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  snapshot = (): UploadItem[] => this.items;

  onCompleted(listener: () => void): () => void {
    this.completedListeners.add(listener);
    return () => this.completedListeners.delete(listener);
  }

  hasFile(id: string): boolean {
    return this.files.has(id);
  }

  add(videoId: string, videoTitle: string, file: File): void {
    const id = crypto.randomUUID();
    this.files.set(id, file);
    this.setItems([
      ...this.items,
      {
        id,
        videoId,
        videoTitle,
        filename: file.name,
        size: file.size,
        state: "queued",
        done: 0,
        uploadId: null,
        error: null,
      },
    ]);
    void this.pump();
  }

  /** The same file picked again resumes where the server stopped receiving it. */
  attachFile(id: string, file: File): void {
    this.files.set(id, file);
    this.hashes.delete(id);
    this.update(id, { filename: file.name, size: file.size, state: "queued", done: 0, error: null });
    void this.pump();
  }

  retry(id: string): void {
    this.update(id, this.files.has(id) ? { state: "queued", done: 0, error: null } : { state: "needs_file" });
    void this.pump();
  }

  /** Removes the entry and cancels its server upload, freeing the slot for the next file. */
  async remove(id: string): Promise<void> {
    const item = this.find(id);
    if (item === undefined) {
      return;
    }
    if (item.uploadId !== null && item.state !== "complete") {
      await cancelUpload(item.uploadId);
    }
    this.files.delete(id);
    this.hashes.delete(id);
    this.setItems(this.items.filter((entry) => entry.id !== id));
    void this.pump();
  }

  /** Cancels a server upload no entry here knows about, e.g. one started on another device. */
  async cancelServerUpload(uploadId: string): Promise<void> {
    await cancelUpload(uploadId);
    void this.pump();
  }

  clearFinished(): void {
    this.setItems(this.items.filter(isOpen));
  }

  private find(id: string): UploadItem | undefined {
    return this.items.find((item) => item.id === id);
  }

  private setItems(items: UploadItem[]): void {
    this.items = items;
    this.store.set(STORAGE_KEY, JSON.stringify(items));
    this.listeners.forEach((listener) => listener());
  }

  private update(id: string, changes: Partial<UploadItem>): void {
    if (this.find(id) !== undefined) {
      this.setItems(this.items.map((item) => (item.id === id ? { ...item, ...changes } : item)));
    }
  }

  private async pump(): Promise<void> {
    if (this.running) {
      this.changedWhileRunning = true;
      return;
    }
    this.changedWhileRunning = false;
    const ready = this.items.filter((item) => item.state === "queued" && this.files.has(item.id));
    // A resumed upload may already hold the server's slot, so it goes before files that haven't started.
    const next = ready.find((item) => item.uploadId !== null) ?? ready[0];
    const file = next && this.files.get(next.id);
    if (next === undefined || file === undefined) {
      return;
    }
    if (this.waitTimer !== null) {
      clearTimeout(this.waitTimer);
      this.waitTimer = null;
    }
    this.running = true;
    let blocked: boolean;
    try {
      blocked = await this.run(next, file);
    } finally {
      this.running = false;
    }
    if (blocked && !this.changedWhileRunning) {
      this.waitTimer = setTimeout(() => {
        this.waitTimer = null;
        void this.pump();
      }, SLOT_POLL_MS);
      return;
    }
    void this.pump();
  }

  /** Returns true when another upload holds the server's slot and this one has to wait. */
  private async run(item: UploadItem, file: File): Promise<boolean> {
    try {
      const active = await fetchActiveUpload();
      if (active !== null && active.id !== item.uploadId) {
        return true;
      }
    } catch {
      return true;
    }
    try {
      let sha256 = this.hashes.get(item.id);
      if (sha256 === undefined) {
        this.update(item.id, { state: "hashing", done: 0 });
        sha256 = await sha256InWorker(file, (done) => this.update(item.id, { done }));
        this.hashes.set(item.id, sha256);
      }
      if (this.find(item.id) === undefined) {
        return false;
      }
      this.update(item.id, { state: "uploading", done: 0 });
      await uploadFile({
        file,
        filename: file.name,
        videoId: item.videoId,
        sha256,
        onStarted: (upload) => this.update(item.id, { uploadId: upload.id }),
        onProgress: (done) => this.update(item.id, { done }),
      });
      this.update(item.id, { state: "complete", done: file.size });
      this.files.delete(item.id);
      this.hashes.delete(item.id);
      this.completedListeners.forEach((listener) => listener());
    } catch (error) {
      if (slotTaken(error)) {
        this.update(item.id, { state: "queued", done: 0 });
        return true;
      }
      const state = error instanceof UploadInterrupted ? "interrupted" : "failed";
      this.update(item.id, { state, error: message(error) });
    }
    return false;
  }
}

export const uploadQueue = new UploadQueue();

export function useUploads(): UploadItem[] {
  return useSyncExternalStore(uploadQueue.subscribe, uploadQueue.snapshot);
}

export function summarizeUploads(items: UploadItem[]): string {
  const count = (states: UploadItemState[]) => items.filter((item) => states.includes(item.state)).length;
  const parts = [
    [count(["hashing", "uploading"]), "uploading"],
    [count(["queued"]), "queued"],
    [count(["interrupted", "needs_file"]), "paused"],
    [count(["failed"]), "failed"],
  ] as const;
  const summary = parts
    .filter(([n]) => n > 0)
    .map(([n, label]) => `${n} ${label}`)
    .join(" · ");
  return summary || "No uploads";
}
