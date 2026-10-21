import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ClipDetail as Clip, ClipReviewIn } from "../api/types";
import { apiError, mockApi } from "../test/fetchMock";
import { clipDetailFixture, clipFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import { installTelegram, removeTelegram } from "../test/telegramStub";
import ClipDetail from "./ClipDetail";

const CLIP = clipFixture().id;
const route = { route: `/clips/${CLIP}`, path: "/clips/:clipId" };
const noLinks = { body: { enabled: false, items: [] } };

afterEach(() => {
  removeTelegram();
});

/** Applies a review the way the API does: the generated title stays, an override shadows it. */
function reviewServer(initial: Clip = clipDetailFixture()) {
  let clip = initial;
  return mockApi({
    [`GET /api/v1/clips/${CLIP}`]: () => ({ body: clip }),
    [`GET /api/v1/clips/${CLIP}/affiliate-links`]: noLinks,
    [`POST /api/v1/clips/${CLIP}/review`]: (call) => {
      const review = call.body as ClipReviewIn;
      const override = review.title_override ?? clip.title_override;
      clip = {
        ...clip,
        review_status: review.decision,
        title_override: override,
        effective_title: override ?? clip.title,
      };
      return { body: clip };
    },
  });
}

describe("ClipDetail", () => {
  it("shows the title, hook, scores, explanation and transcript excerpt", async () => {
    reviewServer();

    renderPage(<ClipDetail />, route);

    expect(await screen.findByRole("heading", { name: "Why sleep beats caffeine" })).toBeInTheDocument();
    expect(screen.getByText("You are drinking coffee wrong.")).toBeInTheDocument();
    expect(screen.getByText("Strong hook and a complete argument.")).toBeInTheDocument();
    expect(screen.getByText("So the thing about caffeine is...")).toBeInTheDocument();
    const scores = screen.getByRole("table", { name: "Scores" });
    expect(within(scores).getByText("hook")).toBeInTheDocument();
    expect(within(scores).getByText("90")).toBeInTheDocument();
    expect(screen.getByText("12:34 – 13:19")).toBeInTheDocument();
  });

  it("opens the Drive file externally through Telegram", async () => {
    const webApp = installTelegram();
    reviewServer();
    renderPage(<ClipDetail />, route);

    await userEvent.click(await screen.findByRole("button", { name: "Open in Google Drive" }));

    expect(webApp.openLink).toHaveBeenCalledWith("https://drive.google.com/file/d/drive-1/view");
    expect(screen.getByText(/signed in to the Google account/)).toBeInTheDocument();
  });

  it("approves with an edited title and keeps the AI suggestion visible", async () => {
    const calls = reviewServer();
    renderPage(<ClipDetail />, route);

    const title = await screen.findByLabelText("Title");
    await userEvent.clear(title);
    await userEvent.type(title, "Sleep > coffee");
    await userEvent.type(screen.getByLabelText("Review note"), "Great hook");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("heading", { name: "Sleep > coffee" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      decision: "approved",
      note: "Great hook",
      title_override: "Sleep > coffee",
    });
    expect(screen.getByText("approved", { selector: ".badge" })).toBeInTheDocument();
    expect(screen.getByText(/AI suggestion/)).toHaveTextContent("Why sleep beats caffeine");
    expect(screen.getByText(/AI suggestion/)).toHaveTextContent("A title already shown in the video keeps this text");
  });

  it("rejects without sending an unchanged title", async () => {
    const calls = reviewServer();
    renderPage(<ClipDetail />, route);

    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));

    expect(await screen.findByText("rejected", { selector: ".badge" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ decision: "rejected", note: null });
  });

  it("shows review errors inline", async () => {
    mockApi({
      [`GET /api/v1/clips/${CLIP}`]: { body: clipDetailFixture() },
      [`GET /api/v1/clips/${CLIP}/affiliate-links`]: noLinks,
      [`POST /api/v1/clips/${CLIP}/review`]: apiError(422, "validation_error", "request validation failed", [
        { loc: ["body", "title_override"], msg: "String should have at most 100 characters", type: "x" },
      ]),
    });
    renderPage(<ClipDetail />, route);

    const title = await screen.findByLabelText("Title");
    await userEvent.type(title, " and more");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("String should have at most 100 characters")).toBeInTheDocument();
  });

  it("copies exactly the caption text exported by the API", async () => {
    const caption = {
      text: "Sleep > coffee\n\nRelated: Sleep mask (Tokopedia) https://tokopedia.link/abc?aff=1\n\nAffiliate link.",
      links: [{ name: "Sleep mask", merchant: "Tokopedia", url: "https://tokopedia.link/abc?aff=1", is_primary: true }],
      disclosure: "Affiliate link.",
    };
    reviewServer(clipDetailFixture({ caption }));
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderPage(<ClipDetail />, route);

    await userEvent.click(await screen.findByRole("button", { name: "Copy caption" }));

    expect(writeText).toHaveBeenCalledWith(caption.text);
    expect(await screen.findByText("Copied.")).toBeInTheDocument();
  });
});
