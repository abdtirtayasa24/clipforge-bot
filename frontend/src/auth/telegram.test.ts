import { afterEach, describe, expect, it } from "vitest";
import { apiError, mockApi } from "../test/fetchMock";
import { api, setAccessToken } from "../api/client";
import { authenticate, getInitData } from "./telegram";
import { installTelegram, removeTelegram } from "../test/telegramStub";

afterEach(() => {
  removeTelegram();
  setAccessToken(null);
});

describe("getInitData", () => {
  it("is null outside Telegram", () => {
    expect(getInitData()).toBeNull();
  });

  it("is null when Telegram provides empty initData (opened by plain URL)", () => {
    installTelegram({ initData: "" });
    expect(getInitData()).toBeNull();
  });

  it("returns the signed initData inside Telegram", () => {
    installTelegram({ initData: "query_id=1&hash=abc" });
    expect(getInitData()).toBe("query_id=1&hash=abc");
  });
});

describe("authenticate", () => {
  it("exchanges initData for a session kept in memory", async () => {
    const calls = mockApi({
      "POST /api/v1/auth/telegram": { body: { access_token: "tok", token_type: "bearer", expires_at: "2026-10-21T12:00:00Z" } },
      "GET /api/v1/dashboard": { body: {} },
    });

    await expect(authenticate("query_id=1&hash=abc")).resolves.toEqual({ status: "ready" });
    await api.get("/dashboard");

    expect(calls[0]?.body).toEqual({ init_data: "query_id=1&hash=abc" });
    expect(calls[1]?.headers.get("Authorization")).toBe("Bearer tok");
  });

  it("reports a non-admin as denied", async () => {
    mockApi({ "POST /api/v1/auth/telegram": apiError(403, "forbidden", "not an administrator") });

    await expect(authenticate("x")).resolves.toEqual({ status: "denied" });
  });

  it("reports rejected initData as failed", async () => {
    mockApi({ "POST /api/v1/auth/telegram": apiError(401, "unauthorized", "invalid initData") });

    await expect(authenticate("x")).resolves.toEqual({ status: "failed", message: "invalid initData" });
  });
});
