// The only place that talks to the network. Handles the CSRF header, error shapes and sign-out.

const BASE = "/api/v1";

export interface FieldError {
  field: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: FieldError[];
  readonly correlationId: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    fields: FieldError[] = [],
    correlationId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
    this.correlationId = correlationId;
  }
}

let csrfToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  query?: Record<string, QueryValue>;
  body?: unknown;
  /** Sent as is, for uploads. The browser streams the file without reading it into memory. */
  rawBody?: Blob;
  signal?: AbortSignal;
  /** Sign-in calls expect a 401 and must not trigger the global sign-out. */
  skipAuthRedirect?: boolean;
}

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return `${BASE}${path}${text ? `?${text}` : ""}`;
}

async function toApiError(response: Response): Promise<ApiError> {
  const correlationId = response.headers.get("x-correlation-id");
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; fields?: FieldError[] };
    };
    return new ApiError(
      response.status,
      body.error?.code ?? "error",
      body.error?.message ?? `The server answered ${response.status}.`,
      body.error?.fields ?? [],
      correlationId,
    );
  } catch {
    return new ApiError(
      response.status,
      "error",
      `The server answered ${response.status}.`,
      [],
      correlationId,
    );
  }
}

export async function request<T>(
  method: string,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.rawBody) headers["Content-Type"] = "application/octet-stream";
  else if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body: options.rawBody ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
      credentials: "same-origin",
      signal: options.signal ?? null,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network", "Cannot reach the server. Check your connection and try again.");
  }

  if (!response.ok) {
    const error = await toApiError(response);
    if (response.status === 401 && !options.skipAuthRedirect) unauthorizedHandler?.();
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>("GET", path, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>("POST", path, { ...options, body: body ?? undefined }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>("PUT", path, { ...options, body: body ?? undefined }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>("PATCH", path, { ...options, body: body ?? undefined }),
  delete: <T>(path: string, options?: RequestOptions) => request<T>("DELETE", path, options),
  /** Uploads a file as the raw request body. The name travels as a query value. */
  upload: <T>(path: string, file: File) =>
    request<T>("POST", path, { query: { filename: file.name }, rawBody: file }),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

/** Field-level messages for forms, keyed by the API's field names. */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {};
  return Object.fromEntries(error.fields.map((item) => [item.field, item.message]));
}
