import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UploadOut } from "../api/types";
import { mockApi } from "../test/fetchMock";
import { renderPage } from "../test/render";
import { uploadFile } from "../upload/chunkedUpload";
import { uploadQueue } from "../upload/uploadQueue";
import Uploads from "./Uploads";

vi.mock("../upload/sha256", () => ({ sha256InWorker: vi.fn(async () => "f".repeat(64)) }));
vi.mock("../upload/chunkedUpload", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../upload/chunkedUpload")>()),
  uploadFile: vi.fn(),
}));

function serverUpload(overrides: Partial<UploadOut> = {}): UploadOut {
  return {
    id: "remote-1",
    video_id: "v9",
    filename: "remote.mp4",
    state: "receiving",
    size_bytes: 2048,
    received_bytes: 1024,
    failure_reason: null,
    created_at: "2026-10-22T10:00:00Z",
    ...overrides,
  };
}

afterEach(async () => {
  for (const item of uploadQueue.snapshot()) {
    await uploadQueue.remove(item.id);
  }
});

describe("Uploads", () => {
  it("shows an empty state", async () => {
    mockApi({ "GET /api/v1/uploads/active": { body: null } });
    renderPage(<Uploads />);

    expect(await screen.findByText("No uploads.")).toBeInTheDocument();
  });

  it("lists the uploading file and the ones waiting in line", async () => {
    mockApi({ "GET /api/v1/uploads/active": { body: null } });
    let finish: () => void = () => undefined;
    vi.mocked(uploadFile).mockImplementation(async (options) => {
      options.onProgress?.(4, 8);
      return new Promise((resolve) => {
        finish = () => resolve(serverUpload({ state: "ready" }));
      });
    });
    uploadQueue.add("v1", "First talk", new File(["01234567"], "a.mp4"));
    uploadQueue.add("v2", "Second talk", new File(["01234567"], "b.mp4"));
    uploadQueue.add("v3", "Third talk", new File(["01234567"], "c.mp4"));

    renderPage(<Uploads />);

    const first = await screen.findByRole("region", { name: "First talk" });
    expect(await within(first).findByText("Uploading…")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Second talk" })).getByText("Queued · #1 in line")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Third talk" })).getByText("Queued · #2 in line")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "First talk" })).toHaveAttribute("href", "/videos/v1");
    vi.mocked(uploadFile).mockResolvedValue(serverUpload({ state: "ready" }));
    finish();
    await vi.waitFor(() => expect(uploadQueue.snapshot().every((item) => item.state === "complete")).toBe(true));
  });

  it("cancels a paused upload this device does not track", async () => {
    let active: UploadOut | null = serverUpload();
    const calls = mockApi({
      "GET /api/v1/uploads/active": () => ({ body: active }),
      "DELETE /api/v1/uploads/remote-1": () => {
        active = null;
        return { body: serverUpload({ state: "failed" }) };
      },
    });
    renderPage(<Uploads />);
    const paused = await screen.findByRole("region", { name: "Paused upload" });
    expect(within(paused).getByText(/remote\.mp4/)).toBeInTheDocument();

    await userEvent.click(within(paused).getByRole("button", { name: "Cancel upload" }));

    expect(await screen.findByText("No uploads.")).toBeInTheDocument();
    expect(calls.some((call) => call.method === "DELETE")).toBe(true);
  });

  it("clears finished uploads", async () => {
    mockApi({ "GET /api/v1/uploads/active": { body: null } });
    vi.mocked(uploadFile).mockResolvedValue(serverUpload({ state: "ready" }));
    uploadQueue.add("v1", "Done talk", new File(["01234567"], "a.mp4"));
    renderPage(<Uploads />);
    expect(await screen.findByText("Upload complete.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Clear finished" }));

    expect(await screen.findByText("No uploads.")).toBeInTheDocument();
  });
});
