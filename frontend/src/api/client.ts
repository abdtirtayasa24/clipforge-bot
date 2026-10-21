const API_BASE = "/api/v1";

export type QueryValue = string | number | boolean | null | undefined;
export type Query = Record<string, QueryValue>;

export interface RequestOptions {
  query?: Query;
  body?: unknown;
  rawBody?: Blob | ArrayBuffer;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

interface ErrorEnvelope {
  error: { code: string; message: string; details: unknown };
}

interface FieldErrorDetail {
  loc: (string | number)[];
  msg: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** Maps FastAPI validation details to `{ "field.path": message }`, dropping the "body"/"query" prefix. */
  fieldErrors(): Record<string, string> {
    if (!Array.isArray(this.details)) {
      return {};
    }
    const errors: Record<string, string> = {};
    for (const detail of this.details as FieldErrorDetail[]) {
      if (!Array.isArray(detail.loc)) {
        continue;
      }
      const path = detail.loc.slice(1).join(".");
      errors[path] ??= detail.msg;
    }
    return errors;
  }
}

let accessToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const search = params.toString();
  return `${API_BASE}${path}${search ? `?${search}` : ""}`;
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  return typeof value === "object" && value !== null && "error" in value;
}

async function toApiError(response: Response): Promise<ApiError> {
  const payload: unknown = await response.json().catch(() => null);
  if (isErrorEnvelope(payload)) {
    const { code, message, details } = payload.error;
    return new ApiError(response.status, code, message, details);
  }
  return new ApiError(response.status, `http_${response.status}`, response.statusText || "Request failed");
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (options.rawBody !== undefined) {
    headers["Content-Type"] = "application/octet-stream";
    body = options.rawBody;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  if (accessToken !== null) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  Object.assign(headers, options.headers);

  const response = await fetch(buildUrl(path, options.query), { method, headers, body, signal: options.signal });
  if (!response.ok) {
    const error = await toApiError(response);
    if (response.status === 401) {
      unauthorizedHandler?.();
    }
    throw error;
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  request,
  get: <T>(path: string, query?: Query) => request<T>("GET", path, { query }),
  post: <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>("POST", path, { body, headers }),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  del: (path: string) => request<undefined>("DELETE", path),
};
