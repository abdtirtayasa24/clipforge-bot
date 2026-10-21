import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { AdminSettings, SettingsPatch } from "../api/types";
import { mockApi } from "../test/fetchMock";
import { productFixture, settingsFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import Affiliate from "./Affiliate";

const products = (items: unknown[]) => ({ body: { items, total: items.length, limit: 200, offset: 0 } });

function settingsServer(initial: AdminSettings = settingsFixture()) {
  let settings = initial;
  return mockApi({
    "GET /api/v1/settings": () => ({ body: settings }),
    "PATCH /api/v1/settings": (call) => {
      settings = { ...settings, ...(call.body as SettingsPatch) } as AdminSettings;
      return { body: settings };
    },
    "GET /api/v1/affiliate/products": products([productFixture(), productFixture({ id: "p2", name: "Old lamp", active: false })]),
    [`DELETE /api/v1/affiliate/products/${productFixture().id}`]: { status: 204 },
  });
}

describe("Affiliate", () => {
  it("turns affiliate links on (default off)", async () => {
    const calls = settingsServer();
    renderPage(<Affiliate />);

    const toggle = await screen.findByRole("checkbox", { name: "Affiliate links" });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ affiliate_links_enabled: true });
    expect(await screen.findByRole("checkbox", { name: "Affiliate links" })).toBeChecked();
  });

  it("saves the relevance threshold and disclosure text", async () => {
    const calls = settingsServer();
    renderPage(<Affiliate />);

    const threshold = await screen.findByLabelText("Minimum relevance (0–1)");
    await userEvent.clear(threshold);
    await userEvent.type(threshold, "0.8");
    const disclosure = screen.getByLabelText("Disclosure text");
    await userEvent.clear(disclosure);
    await userEvent.type(disclosure, "Contains affiliate links.");
    await userEvent.click(screen.getByRole("button", { name: "Save affiliate settings" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      affiliate_min_relevance: 0.8,
      affiliate_disclosure_text: "Contains affiliate links.",
    });
  });

  it("lists the catalog and deactivates a product", async () => {
    const calls = settingsServer();
    renderPage(<Affiliate />);

    const catalog = await screen.findByRole("list", { name: "Products" });
    expect(within(catalog).getByRole("link", { name: /Sleep mask/ })).toHaveAttribute(
      "href",
      `/affiliate/products/${productFixture().id}`,
    );
    expect(within(catalog).getByText("inactive")).toBeInTheDocument();

    await userEvent.click(within(catalog).getByRole("button", { name: "Deactivate Sleep mask" }));

    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });
});
