import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { apiError, mockApi } from "../test/fetchMock";
import { dashboardFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import Dashboard from "./Dashboard";

describe("Dashboard", () => {
  it("shows a loading state first", () => {
    mockApi({ "GET /api/v1/dashboard": { body: dashboardFixture() } });
    renderPage(<Dashboard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });

  it("shows job counts, review queue and estimated AI costs with currency", async () => {
    mockApi({ "GET /api/v1/dashboard": { body: dashboardFixture() } });

    renderPage(<Dashboard />);

    const review = await screen.findByRole("link", { name: /Clips awaiting review/ });
    expect(review).toHaveAttribute("href", "/clips");
    expect(review).toHaveTextContent("3");
    const costs = screen.getByRole("region", { name: "Estimated AI cost" });
    expect(within(costs).getAllByText("USD 0.42").length).toBeGreaterThan(0);
    expect(within(costs).getByText("USD 1.75")).toBeInTheDocument();
    expect(within(costs).getByText("USD 0.105")).toBeInTheDocument();
    expect(within(costs).getAllByText("gemini / gemini-3.5-flash-lite").length).toBeGreaterThan(0);
    expect(within(costs).getByText(/estimates/i)).toBeInTheDocument();
  });

  it("shows an empty cost state when nothing was spent", async () => {
    const empty = { totals: [], by_day: [], by_model: [], by_stage: [] };
    mockApi({
      "GET /api/v1/dashboard": {
        body: dashboardFixture({
          costs: { last_7_days: empty, last_30_days: empty, average_per_completed_job: [], completed_jobs: 0 },
        }),
      },
    });

    renderPage(<Dashboard />);

    expect(await screen.findAllByText("No AI usage recorded.")).not.toHaveLength(0);
  });

  it("shows the API error", async () => {
    mockApi({ "GET /api/v1/dashboard": apiError(503, "service_unavailable", "database unavailable") });

    renderPage(<Dashboard />);

    expect(await screen.findByRole("alert")).toHaveTextContent("database unavailable");
  });
});
