import { vi } from "vitest";

export interface RecordedCall {
  method: string;
  path: string;
  query: URLSearchParams;
  headers: Headers;
  body: unknown;
}

type Reply = { status?: number; body?: unknown };
type Handler = Reply | ((call: RecordedCall) => Reply);

export function jsonResponse(status: number, body?: unknown): Response {
  if (status === 204 || body === undefined) {
    return new Response(null, { status });
  }
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function apiError(status: number, code: string, message: string, details: unknown = null): Reply {
  return { status, body: { error: { code, message, details } } };
}

/** Routes are keyed "METHOD /api/v1/path"; unmatched requests fail the test loudly. */
export function mockApi(routes: Record<string, Handler>): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = (init?.method ?? "GET").toUpperCase();
      const rawBody = init?.body;
      const call: RecordedCall = {
        method,
        path: url.pathname,
        query: url.searchParams,
        headers: new Headers(init?.headers),
        body: typeof rawBody === "string" ? JSON.parse(rawBody) : rawBody,
      };
      calls.push(call);
      const handler = routes[`${method} ${url.pathname}`];
      if (handler === undefined) {
        throw new Error(`unmocked request ${method} ${url.pathname}`);
      }
      const reply = typeof handler === "function" ? handler(call) : handler;
      return jsonResponse(reply.status ?? 200, reply.body);
    }),
  );
  return calls;
}
