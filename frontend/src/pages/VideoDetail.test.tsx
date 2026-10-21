import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "../test/fetchMock";
import { jobFixture, videoFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import { installTelegram, removeTelegram } from "../test/telegramStub";
import { UploadInterrupted, uploadFile } from "../upload/chunkedUpload";
import { youtubeWatchUrl } from "../youtube";
import VideoDetail from "./VideoDetail";

vi.mock("../upload/sha256", () => ({ sha256InWorker: vi.fn(async () => "f".repeat(64)) }));
vi.mock("../upload/chunkedUpload", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../upload/chunkedUpload")>()),
  uploadFile: vi.fn(),
}));

const VIDEO = videoFixture().id;
const route = { route: `/videos/${VIDEO}`, path: "/videos/:videoId" };
const noJobs = { body: { items: [], total: 0, limit: 20, offset: 0 } };

beforeEach(() => {
  vi.mocked(uploadFile).mockReset();
});

afterEach(() => {
  removeTelegram();
});

describe("VideoDetail", () => {
  it("opens the video on YouTube and copies its watch URL", async () => {
    const webApp = installTelegram();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const video = videoFixture();
    const url = "https://www.youtube.com/watch?v=" + video.youtube_video_id;
    mockApi({
      [`GET /api/v1/videos/${VIDEO}`]: { body: video },
      "GET /api/v1/jobs": noJobs,
    });
    renderPage(<VideoDetail />, route);

    expect(await screen.findByText(url)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Watch on YouTube" }));
    expect(webApp.openLink).toHaveBeenCalledWith(url);
    await userEvent.click(screen.getByRole("button", { name: "Copy URL" }));
    expect(writeText).toHaveBeenCalledWith(url);
    expect(await screen.findByText("URL copied.")).toBeInTheDocument();
  });

  it("builds a safe watch URL from the video id", () => {
    expect(youtubeWatchUrl("dQw4w9WgXcQ")).toBe("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
    expect(youtubeWatchUrl("a&b=c")).toBe("https://www.youtube.com/watch?v=a%26b%3Dc");
  });

  it("registers a URL without permission evidence", async () => {
    const calls = mockApi({
      [`GET /api/v1/videos/${VIDEO}`]: { body: videoFixture() },
      "GET /api/v1/jobs": noJobs,
      [`POST /api/v1/videos/${VIDEO}/media`]: {
        status: 201,
        body: { id: "m1", video_id: VIDEO, kind: "url", source_url: null, state: "pending", created_at: "x" },
      },
    });
    renderPage(<VideoDetail />, route);

    await userEvent.type(await screen.findByLabelText("Permitted URL"), "https://cdn.example.com/talk.mp4");
    await userEvent.click(screen.getByRole("button", { name: "Register URL" }));

    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ url: "https://cdn.example.com/talk.mp4" });
    expect(screen.queryByText(/Permission evidence/)).not.toBeInTheDocument();
    expect(await screen.findByText("URL registered.")).toBeInTheDocument();
  });

  it("enables upload once a file is chosen, then uploads with progress", async () => {
    mockApi({ [`GET /api/v1/videos/${VIDEO}`]: { body: videoFixture() }, "GET /api/v1/jobs": noJobs });
    vi.mocked(uploadFile).mockImplementation(async (options) => {
      options.onProgress?.(4, 8);
      return {
        id: "u1",
        video_id: VIDEO,
        filename: "talk.mp4",
        state: "ready",
        size_bytes: 8,
        received_bytes: 8,
        failure_reason: null,
        created_at: "x",
      };
    });
    renderPage(<VideoDetail />, route);

    const submit = await screen.findByRole("button", { name: "Upload" });
    expect(submit).toBeDisabled();
    const file = new File(["01234567"], "talk.mp4", { type: "video/mp4" });
    await userEvent.upload(screen.getByLabelText("Video file"), file);
    await userEvent.click(submit);

    expect(await screen.findByText("Upload complete.")).toBeInTheDocument();
    const options = vi.mocked(uploadFile).mock.calls[0]?.[0];
    expect(options).toMatchObject({ file, filename: "talk.mp4", videoId: VIDEO, sha256: "f".repeat(64) });
    expect(options).not.toHaveProperty("permission");
  });

  it("offers to resume an interrupted upload", async () => {
    mockApi({ [`GET /api/v1/videos/${VIDEO}`]: { body: videoFixture() }, "GET /api/v1/jobs": noJobs });
    vi.mocked(uploadFile).mockRejectedValueOnce(new UploadInterrupted("u1", 4, new TypeError("offline")));
    renderPage(<VideoDetail />, route);
    await userEvent.upload(await screen.findByLabelText("Video file"), new File(["01234567"], "talk.mp4", { type: "video/mp4" }));

    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Upload paused");
    expect(screen.getByRole("button", { name: "Resume upload" })).toBeEnabled();
  });

  it("queues a job with an Idempotency-Key and lists the video's jobs", async () => {
    const calls = mockApi({
      [`GET /api/v1/videos/${VIDEO}`]: { body: videoFixture() },
      "GET /api/v1/jobs": noJobs,
      [`POST /api/v1/videos/${VIDEO}/jobs`]: { status: 201, body: jobFixture({ status: "awaiting_source" }) },
    });
    renderPage(<VideoDetail />, route);

    await userEvent.click(await screen.findByRole("button", { name: "Queue processing job" }));

    const post = calls.find((c) => c.method === "POST");
    expect(post?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(calls.filter((c) => c.path === "/api/v1/jobs").at(-1)?.query.get("video_id")).toBe(VIDEO);
  });
});
