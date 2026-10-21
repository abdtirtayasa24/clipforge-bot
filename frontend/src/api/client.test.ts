import { afterEach, describe, expect, it, vi } from "vitest";
import { apiError, mockApi } from "../test/fetchMock";
import { ApiError, api, setAccessToken, setUnauthorizedHandler } from "./client";

afterEach(() => {
  setAccessToken(null);
  setUnauthorizedHandler(null);
});

describe("api client", () => {
  it("sends the bearer token and JSON body", async () => {
    const calls = mockApi({ "POST /api/v1/sources": { status: 201, body: { id: "s1" } } });
    setAccessToken("tok");

    const result = await api.post<{ id: string }>("/sources", { name: "x" });

    expect(result).toEqual({ id: "s1" });
    expect(calls[0]?.headers.get("Authorization")).toBe("Bearer tok");
    expect(calls[0]?.headers.get("Content-Type")).toBe("application/json");
    expect(calls[0]?.body).toEqual({ name: "x" });
  });

  it("omits empty query values", async () => {
    const calls = mockApi({ "GET /api/v1/jobs": { body: { items: [] } } });

    await api.get("/jobs", { status: "running", video_id: undefined, offset: 0, limit: null });

    expect(calls[0]?.query.toString()).toBe("status=running&offset=0");
  });

  it("returns undefined for 204 responses", async () => {
    mockApi({ "DELETE /api/v1/sources/s1": { status: 204 } });

    await expect(api.del("/sources/s1")).resolves.toBeUndefined();
  });

  it("raises ApiError from the error envelope with field errors", async () => {
    mockApi({
      "PATCH /api/v1/settings": apiError(422, "validation_error", "request validation failed", [
        { loc: ["body", "min_score"], msg: "Input should be less than or equal to 100", type: "x" },
      ]),
    });

    const error = await api.patch("/settings", { min_score: 200 }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    const apiErr = error as ApiError;
    expect(apiErr.status).toBe(422);
    expect(apiErr.code).toBe("validation_error");
    expect(apiErr.fieldErrors()).toEqual({ min_score: "Input should be less than or equal to 100" });
  });

  it("falls back to the status text when the body is not an envelope", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>", { status: 502, statusText: "Bad Gateway" })));

    const error = (await api.get("/dashboard").catch((e: unknown) => e)) as ApiError;

    expect(error.status).toBe(502);
    expect(error.code).toBe("http_502");
    expect(error.message).toBe("Bad Gateway");
  });

  it("notifies the unauthorized handler on 401", async () => {
    mockApi({ "GET /api/v1/dashboard": apiError(401, "unauthorized", "invalid or expired session") });
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);

    await expect(api.get("/dashboard")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("passes raw bodies and extra headers through untouched", async () => {
    const calls = mockApi({ "PATCH /api/v1/uploads/u1": { body: { received_bytes: 3 } } });
    const chunk = new Blob(["abc"]);

    await api.request("PATCH", "/uploads/u1", { rawBody: chunk, headers: { "Content-Range": "bytes 0-2/3" } });

    expect(calls[0]?.body).toBe(chunk);
    expect(calls[0]?.headers.get("Content-Range")).toBe("bytes 0-2/3");
    expect(calls[0]?.headers.get("Content-Type")).toBe("application/octet-stream");
  });
});
