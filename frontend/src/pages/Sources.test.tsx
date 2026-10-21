import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SourceOut } from "../api/types";
import { apiError, mockApi } from "../test/fetchMock";
import { renderPage } from "../test/render";
import Sources from "./Sources";

function source(overrides: Partial<SourceOut> = {}): SourceOut {
  return {
    id: "s1",
    kind: "trending",
    name: "Trending ID",
    channel_id: null,
    region_code: "ID",
    enabled: true,
    last_run_at: null,
    created_at: "2026-10-21T10:00:00Z",
    updated_at: "2026-10-21T10:00:00Z",
    ...overrides,
  };
}

describe("Sources", () => {
  it("lists sources and shows an empty state", async () => {
    mockApi({ "GET /api/v1/sources": { body: { items: [] } } });
    renderPage(<Sources />);
    expect(await screen.findByText("No sources yet.")).toBeInTheDocument();
  });

  it("creates a channel source with its channel id", async () => {
    const calls = mockApi({
      "GET /api/v1/sources": { body: { items: [] } },
      "POST /api/v1/sources": { status: 201, body: source({ kind: "channel" }) },
    });
    renderPage(<Sources />);
    await screen.findByText("No sources yet.");
    await userEvent.click(screen.getByRole("button", { name: "New source" }));

    await userEvent.selectOptions(screen.getByLabelText("Kind"), "channel");
    await userEvent.type(screen.getByLabelText("Name"), "My channel");
    await userEvent.type(screen.getByLabelText("Channel ID"), "UC1234567890123456789012");
    await userEvent.click(screen.getByRole("button", { name: "Add source" }));

    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ kind: "channel", name: "My channel", channel_id: "UC1234567890123456789012" });
  });

  it("shows API validation errors next to the field", async () => {
    mockApi({
      "GET /api/v1/sources": { body: { items: [] } },
      "POST /api/v1/sources": apiError(422, "validation_error", "request validation failed", [
        { loc: ["body", "region_code"], msg: "String should match pattern '^[A-Z]{2}$'", type: "x" },
      ]),
    });
    renderPage(<Sources />);
    await screen.findByText("No sources yet.");
    await userEvent.click(screen.getByRole("button", { name: "New source" }));

    await userEvent.type(screen.getByLabelText("Name"), "Trending");
    await userEvent.clear(screen.getByLabelText("Region"));
    await userEvent.type(screen.getByLabelText("Region"), "id");
    await userEvent.click(screen.getByRole("button", { name: "Add source" }));

    expect(await screen.findByText("String should match pattern '^[A-Z]{2}$'")).toBeInTheDocument();
  });

  it("disables and re-enables a source", async () => {
    const calls = mockApi({
      "GET /api/v1/sources": { body: { items: [source()] } },
      "PATCH /api/v1/sources/s1": { body: source({ enabled: false }) },
    });
    renderPage(<Sources />);

    const item = (await screen.findByText("Trending ID")).closest("li");
    await userEvent.click(within(item as HTMLElement).getByRole("checkbox", { name: "Enabled" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ enabled: false });
  });

  it("filters the list by source kind", async () => {
    mockApi({
      "GET /api/v1/sources": {
        body: { items: [source(), source({ id: "s2", kind: "channel", name: "My channel", channel_id: "UC1" })] },
      },
    });
    renderPage(<Sources />);
    await screen.findByText("My channel");

    await userEvent.click(screen.getByRole("button", { name: "Channels" }));

    expect(screen.getByText("My channel")).toBeInTheDocument();
    expect(screen.queryByText("Trending ID")).not.toBeInTheDocument();
  });

  it("starts a discovery run and reports a conflict", async () => {
    mockApi({
      "GET /api/v1/sources": { body: { items: [source()] } },
      "POST /api/v1/discovery/run": apiError(409, "conflict", "discovery is already running"),
    });
    renderPage(<Sources />);

    await userEvent.click(await screen.findByRole("button", { name: "Run discovery now" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("discovery is already running");
  });
});
