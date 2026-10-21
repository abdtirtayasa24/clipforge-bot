import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { AffiliateLinkOut } from "../api/types";
import { apiError, mockApi } from "../test/fetchMock";
import { linkFixture, productFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import { AffiliateSuggestions } from "./AffiliateSuggestions";

const CLIP = linkFixture().clip_id;
const LINK = linkFixture().id;
const LINKS = `/api/v1/clips/${CLIP}/affiliate-links`;
const list = (enabled: boolean, items: AffiliateLinkOut[]) => ({ body: { enabled, items } });
const products = { body: { items: [productFixture({ id: "p2", name: "Pillow" })], total: 1, limit: 200, offset: 0 } };

describe("AffiliateSuggestions", () => {
  it("shows suggestions with relevance and reason, and approves one", async () => {
    const calls = mockApi({
      [`GET ${LINKS}`]: list(true, [linkFixture()]),
      "GET /api/v1/affiliate/products": products,
      [`POST ${LINKS}/${LINK}/review`]: list(true, [linkFixture({ status: "approved", is_primary: true })]),
    });
    renderPage(<AffiliateSuggestions clipId={CLIP} />);

    expect(await screen.findByText("Sleep mask")).toBeInTheDocument();
    expect(screen.getByText("Clip discusses sleep quality")).toBeInTheDocument();
    expect(screen.getByText("relevance 0.83")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Approve Sleep mask" }));

    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ decision: "approved" });
    expect(await screen.findByText("primary")).toBeInTheDocument();
  });

  it("sets an approved link as primary and rejects another", async () => {
    const approved = linkFixture({ status: "approved" });
    const other = linkFixture({ id: "l2", product: productFixture({ id: "p2", name: "Pillow" }) });
    const calls = mockApi({
      [`GET ${LINKS}`]: list(true, [approved, other]),
      "GET /api/v1/affiliate/products": products,
      [`POST ${LINKS}/${LINK}/review`]: list(true, [{ ...approved, is_primary: true }, other]),
      [`POST ${LINKS}/l2/review`]: list(true, [approved, { ...other, status: "rejected" }]),
    });
    renderPage(<AffiliateSuggestions clipId={CLIP} />);

    await userEvent.click(await screen.findByRole("button", { name: "Make Sleep mask primary" }));
    await userEvent.click(screen.getByRole("button", { name: "Reject Pillow" }));

    const posts = calls.filter((c) => c.method === "POST").map((c) => c.body);
    expect(posts).toEqual([{ decision: "approved", primary: true }, { decision: "rejected" }]);
  });

  it("picks a catalog product manually and reruns matching", async () => {
    const calls = mockApi({
      [`GET ${LINKS}`]: list(true, []),
      "GET /api/v1/affiliate/products": products,
      [`POST ${LINKS}`]: { status: 201, body: { enabled: true, items: [] } },
      [`POST ${LINKS}/rematch`]: list(true, []),
    });
    renderPage(<AffiliateSuggestions clipId={CLIP} />);
    expect(await screen.findByText("No suggestions. Matching never invents links.")).toBeInTheDocument();

    await userEvent.selectOptions(await screen.findByLabelText("Catalog product"), "p2");
    await userEvent.click(screen.getByRole("button", { name: "Add product" }));
    await userEvent.click(screen.getByRole("button", { name: "Rematch" }));

    const posts = calls.filter((c) => c.method === "POST");
    expect(posts[0]?.body).toEqual({ product_id: "p2" });
    expect(posts[1]?.path).toBe(`${LINKS}/rematch`);
  });

  it("hides review controls when affiliate links are off but still allows removal", async () => {
    const calls = mockApi({
      [`GET ${LINKS}`]: list(false, [linkFixture({ status: "approved", is_primary: true })]),
      [`DELETE ${LINKS}/${LINK}`]: { status: 204 },
    });
    renderPage(<AffiliateSuggestions clipId={CLIP} />);

    expect(await screen.findByText(/Affiliate links are off/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve|Reject|Rematch|primary/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Catalog product")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Remove Sleep mask" }));

    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("shows API errors", async () => {
    mockApi({
      [`GET ${LINKS}`]: list(true, []),
      "GET /api/v1/affiliate/products": products,
      [`POST ${LINKS}/rematch`]: apiError(409, "conflict", "affiliate links are disabled"),
    });
    renderPage(<AffiliateSuggestions clipId={CLIP} />);

    await userEvent.click(await screen.findByRole("button", { name: "Rematch" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("affiliate links are disabled");
  });
});
