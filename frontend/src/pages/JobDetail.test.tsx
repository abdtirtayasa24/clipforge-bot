import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { JobStatus } from "../api/types";
import { apiError, mockApi } from "../test/fetchMock";
import { jobDetailFixture, jobFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import { installTelegram, removeTelegram } from "../test/telegramStub";
import JobDetail from "./JobDetail";

const JOB = jobFixture().id;
const route = { route: `/jobs/${JOB}`, path: "/jobs/:jobId" };

afterEach(() => {
  removeTelegram();
});

describe("JobDetail", () => {
  it("shows the stage timeline, attempts and per-stage estimated costs", async () => {
    mockApi({ [`GET /api/v1/jobs/${JOB}`]: { body: jobDetailFixture() } });

    renderPage(<JobDetail />, route);

    const timeline = await screen.findByRole("list", { name: "Stage timeline" });
    expect(within(timeline).getByText("queued")).toBeInTheDocument();
    expect(within(timeline).getByText("1m 0s")).toBeInTheDocument();
    expect(screen.getByText("1 / 5")).toBeInTheDocument();
    const costs = screen.getByRole("region", { name: "Estimated AI cost" });
    expect(within(costs).getByText("USD 0.0015")).toBeInTheDocument();
    expect(within(costs).getByText("USD 0.001")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("retries a failed job after confirmation and shows the server's new state", async () => {
    installTelegram();
    let status: JobStatus = "failed";
    const calls = mockApi({
      [`GET /api/v1/jobs/${JOB}`]: () => ({
        body: jobDetailFixture({ status, last_error: status === "failed" ? "ffmpeg crashed" : null }),
      }),
      [`POST /api/v1/jobs/${JOB}/retry`]: () => {
        status = "queued";
        return { body: jobFixture({ status: "queued" }) };
      },
    });
    renderPage(<JobDetail />, route);
    expect(await screen.findByText("ffmpeg crashed")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("queued", { selector: ".badge" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/retry"))).toBe(true);
    expect(screen.queryByText("ffmpeg crashed")).not.toBeInTheDocument();
  });

  it("does not cancel when the confirmation is declined", async () => {
    installTelegram({ showConfirm: (_message, callback) => callback?.(false) });
    const calls = mockApi({ [`GET /api/v1/jobs/${JOB}`]: { body: jobDetailFixture() } });
    renderPage(<JobDetail />, route);

    await userEvent.click(await screen.findByRole("button", { name: "Cancel job" }));

    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("shows a conflict from the server instead of pretending the action worked", async () => {
    installTelegram();
    mockApi({
      [`GET /api/v1/jobs/${JOB}`]: { body: jobDetailFixture() },
      [`POST /api/v1/jobs/${JOB}/cancel`]: apiError(409, "conflict", "job is already completed"),
    });
    renderPage(<JobDetail />, route);

    await userEvent.click(await screen.findByRole("button", { name: "Cancel job" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("job is already completed");
  });

  it("shows not found", async () => {
    mockApi({ [`GET /api/v1/jobs/${JOB}`]: apiError(404, "not_found", "job not found") });
    renderPage(<JobDetail />, route);
    expect(await screen.findByRole("alert")).toHaveTextContent("job not found");
  });
});
