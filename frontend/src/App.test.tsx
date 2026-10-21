import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import App from "./App";
import { setAccessToken } from "./api/client";
import { apiError, mockApi } from "./test/fetchMock";
import { installTelegram, removeTelegram } from "./test/telegramStub";

afterEach(() => {
  removeTelegram();
  setAccessToken(null);
});

function renderApp() {
  return render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
}

describe("App auth gate", () => {
  it("asks to open the app from Telegram when there is no initData", () => {
    renderApp();
    expect(screen.getByRole("heading", { name: "Open from Telegram" })).toBeInTheDocument();
  });

  it("shows access denied for a non-admin", async () => {
    installTelegram();
    mockApi({ "POST /api/v1/auth/telegram": apiError(403, "forbidden", "not an administrator") });

    renderApp();

    expect(await screen.findByRole("heading", { name: "Access denied" })).toBeInTheDocument();
  });

  it("shows the sign-in error when initData is rejected", async () => {
    installTelegram();
    mockApi({ "POST /api/v1/auth/telegram": apiError(401, "unauthorized", "invalid initData") });

    renderApp();

    expect(await screen.findByRole("heading", { name: "Sign-in failed" })).toBeInTheDocument();
    expect(screen.getByText("invalid initData")).toBeInTheDocument();
  });

  it("signs in the admin, tells Telegram it is ready and shows navigation", async () => {
    const webApp = installTelegram();
    mockApi({
      "POST /api/v1/auth/telegram": { body: { access_token: "tok", token_type: "bearer", expires_at: "2026-10-21T12:00:00Z" } },
    });

    renderApp();

    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(webApp.ready).toHaveBeenCalled();
    expect(webApp.expand).toHaveBeenCalled();
  });
});
