import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/fetchMock";
import { clipFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import Clips from "./Clips";

const page = (items: unknown[]) => ({ body: { items, total: items.length, limit: 20, offset: 0 } });

describe("Clips", () => {
  it("opens on clips awaiting review and shows effective titles", async () => {
    const edited = clipFixture({ title_override: "Sleep > coffee", effective_title: "Sleep > coffee" });
    const calls = mockApi({ "GET /api/v1/clips": page([edited]) });

    renderPage(<Clips />);

    const link = await screen.findByRole("link", { name: /Sleep > coffee/ });
    expect(link).toHaveAttribute("href", `/clips/${edited.id}`);
    expect(screen.queryByText("Why sleep beats caffeine")).not.toBeInTheDocument();
    expect(calls[0]?.query.get("status")).toBe("pending_review");
  });

  it("switches to all clips", async () => {
    const calls = mockApi({ "GET /api/v1/clips": page([]) });
    renderPage(<Clips />);
    await screen.findByText("No clips awaiting review.");

    await userEvent.click(screen.getByRole("button", { name: "All" }));

    await screen.findByText("No clips.");
    expect(calls.at(-1)?.query.has("status")).toBe(false);
  });
});
