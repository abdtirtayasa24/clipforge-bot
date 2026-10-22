import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { apiError, mockApi } from "../test/fetchMock";
import { jobListItemFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import Jobs from "./Jobs";

const page = (items: unknown[]) => ({ body: { items, total: items.length, limit: 20, offset: 0 } });

describe("Jobs", () => {
  it("lists jobs linking to their detail", async () => {
    mockApi({ "GET /api/v1/jobs": page([jobListItemFixture()]) });

    renderPage(<Jobs />);

    const link = await screen.findByRole("link", { name: /transcribe/ });
    expect(link).toHaveAttribute("href", `/jobs/${jobListItemFixture().id}`);
    expect(link).toHaveTextContent("running");
  });

  it("links each job to its source video", async () => {
    mockApi({ "GET /api/v1/jobs": page([jobListItemFixture()]) });

    renderPage(<Jobs />);

    const link = await screen.findByRole("link", { name: "Masa depan AI" });
    expect(link).toHaveAttribute("href", `/videos/${jobListItemFixture().video_id}`);
  });

  it("filters by status through the API", async () => {
    const calls = mockApi({ "GET /api/v1/jobs": page([]) });
    renderPage(<Jobs />);
    await screen.findByText("No jobs.");

    await userEvent.click(screen.getByRole("button", { name: "Failed" }));

    await screen.findByText("No jobs.");
    expect(calls.at(-1)?.query.get("status")).toBe("failed");
  });

  it("shows the API error", async () => {
    mockApi({ "GET /api/v1/jobs": apiError(500, "internal_error", "internal server error") });
    renderPage(<Jobs />);
    expect(await screen.findByRole("alert")).toHaveTextContent("internal server error");
  });
});
