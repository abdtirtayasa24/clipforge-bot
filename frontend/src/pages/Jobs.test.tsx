import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { apiError, mockApi } from "../test/fetchMock";
import { jobFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import Jobs from "./Jobs";

const page = (items: unknown[]) => ({ body: { items, total: items.length, limit: 20, offset: 0 } });

describe("Jobs", () => {
  it("lists jobs linking to their detail", async () => {
    mockApi({ "GET /api/v1/jobs": page([jobFixture()]) });

    renderPage(<Jobs />);

    const link = await screen.findByRole("link", { name: /transcribe/ });
    expect(link).toHaveAttribute("href", `/jobs/${jobFixture().id}`);
    expect(link).toHaveTextContent("running");
  });

  it("filters by status through the API", async () => {
    const calls = mockApi({ "GET /api/v1/jobs": page([]) });
    renderPage(<Jobs />);
    await screen.findByText("No jobs.");

    await userEvent.selectOptions(screen.getByLabelText("Status"), "failed");

    await screen.findByText("No jobs.");
    expect(calls.at(-1)?.query.get("status")).toBe("failed");
  });

  it("shows the API error", async () => {
    mockApi({ "GET /api/v1/jobs": apiError(500, "internal_error", "internal server error") });
    renderPage(<Jobs />);
    expect(await screen.findByRole("alert")).toHaveTextContent("internal server error");
  });
});
