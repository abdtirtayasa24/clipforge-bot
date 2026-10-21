import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { apiError, mockApi } from "../test/fetchMock";
import { productFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import AffiliateProductForm from "./AffiliateProductForm";

const PRODUCT = productFixture().id;
const products = { body: { items: [productFixture()], total: 1, limit: 200, offset: 0 } };

async function fillRequired() {
  await userEvent.type(screen.getByLabelText("Name"), "Sleep mask");
  await userEvent.type(screen.getByLabelText("Merchant"), "Tokopedia");
  await userEvent.type(screen.getByLabelText("Affiliate URL"), "https://tokopedia.link/abc");
  await userEvent.type(screen.getByLabelText("Destination domain"), "tokopedia.link");
}

describe("AffiliateProductForm", () => {
  it("creates a product with tags split by comma", async () => {
    const calls = mockApi({ "POST /api/v1/affiliate/products": { status: 201, body: productFixture() } });
    renderPage(<AffiliateProductForm />, { route: "/affiliate/products/new", path: "/affiliate/products/new" });

    await fillRequired();
    await userEvent.type(screen.getByLabelText("Tags (comma separated)"), "sleep, health ,");
    await userEvent.click(screen.getByRole("button", { name: "Save product" }));

    expect(calls[0]?.body).toEqual({
      name: "Sleep mask",
      merchant: "Tokopedia",
      affiliate_url: "https://tokopedia.link/abc",
      destination_domain: "tokopedia.link",
      category: null,
      tags: ["sleep", "health"],
      language_code: null,
      region_code: null,
      active: true,
    });
    expect(await screen.findByText("other page")).toBeInTheDocument();
  });

  it("shows URL validation errors from the API inline", async () => {
    mockApi({
      "POST /api/v1/affiliate/products": apiError(422, "validation_error", "request validation failed", [
        { loc: ["body", "affiliate_url"], msg: "affiliate_url must use https", type: "value_error" },
      ]),
    });
    renderPage(<AffiliateProductForm />, { route: "/affiliate/products/new", path: "/affiliate/products/new" });

    await fillRequired();
    await userEvent.click(screen.getByRole("button", { name: "Save product" }));

    expect(await screen.findByText("affiliate_url must use https")).toBeInTheDocument();
  });

  it("edits an existing product", async () => {
    const calls = mockApi({
      "GET /api/v1/affiliate/products": products,
      [`PATCH /api/v1/affiliate/products/${PRODUCT}`]: { body: productFixture({ name: "Silk sleep mask" }) },
    });
    renderPage(<AffiliateProductForm />, { route: `/affiliate/products/${PRODUCT}`, path: "/affiliate/products/:productId" });

    const name = await screen.findByLabelText("Name");
    expect(name).toHaveValue("Sleep mask");
    await userEvent.clear(name);
    await userEvent.type(name, "Silk sleep mask");
    await userEvent.click(screen.getByRole("button", { name: "Save product" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ name: "Silk sleep mask", tags: ["sleep"] });
  });
});
