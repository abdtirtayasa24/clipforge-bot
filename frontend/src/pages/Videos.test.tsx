import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/fetchMock";
import { videoFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import Videos from "./Videos";

const page = (items: unknown[]) => ({ body: { items, total: items.length, limit: 20, offset: 0 } });

describe("Videos", () => {
  it("lists videos with eligibility, rights and source status", async () => {
    mockApi({ "GET /api/v1/videos": page([videoFixture()]) });

    renderPage(<Videos />);

    const link = await screen.findByRole("link", { name: /Long podcast/ });
    expect(link).toHaveAttribute("href", `/videos/${videoFixture().id}`);
    expect(link).toHaveTextContent("eligible");
    expect(link).toHaveTextContent("unverified");
    expect(link).toHaveTextContent("awaiting source");
  });

  it("filters by rights status", async () => {
    const calls = mockApi({ "GET /api/v1/videos": page([]) });
    renderPage(<Videos />);
    await screen.findByText("No videos.");

    await userEvent.selectOptions(screen.getByLabelText("Rights"), "authorized");

    await screen.findByText("No videos.");
    expect(calls.at(-1)?.query.get("rights_status")).toBe("authorized");
  });
});
