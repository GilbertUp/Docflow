import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  buildUrl,
  errorMessage,
  fieldErrors,
  onUnauthorized,
  setCsrfToken,
} from "./client";

function reply(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Correlation-ID": "abc-123" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  onUnauthorized(null);
});

describe("buildUrl", () => {
  it("prefixes the version and keeps only meaningful query values", () => {
    expect(buildUrl("/users", { limit: 25, search: "", offset: 0, kind: undefined, flag: false })).toBe(
      "/api/v1/users?limit=25&offset=0&flag=false",
    );
  });

  it("leaves out the query string when there is nothing to send", () => {
    expect(buildUrl("/cabinets")).toBe("/api/v1/cabinets");
  });
});

describe("request", () => {
  it("sends the CSRF token on changes but not on reads", async () => {
    const fetchMock = vi.fn(async () => reply(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("token-1");

    await api.get("/cabinets");
    await api.post("/cabinets", { name: "Finance" });

    const [readInit, writeInit] = fetchMock.mock.calls.map((call) => (call as unknown as [string, RequestInit])[1]);
    expect(new Headers(readInit?.headers).has("X-CSRF-Token")).toBe(false);
    expect(new Headers(writeInit?.headers).get("X-CSRF-Token")).toBe("token-1");
    expect(new Headers(writeInit?.headers).get("Content-Type")).toBe("application/json");
    expect(writeInit?.body).toBe(JSON.stringify({ name: "Finance" }));
  });

  it("uploads a file as the raw body with its name in the query", async () => {
    const fetchMock = vi.fn(async () => reply(201, { duplicate: false }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("token-2");
    const file = new File(["%PDF-1.7"], "ABC invoice.pdf", { type: "application/pdf" });

    await api.upload("/folders/f1/documents", file);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/folders/f1/documents?filename=ABC+invoice.pdf");
    expect(init.body).toBe(file);
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).toBe("application/octet-stream");
    expect(headers.get("X-CSRF-Token")).toBe("token-2");
  });

  it("returns nothing for 204 answers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(204)));
    await expect(api.delete("/folders/1")).resolves.toBeUndefined();
  });

  it("turns the server's error shape into an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        reply(422, {
          error: {
            code: "validation_error",
            message: "The request is not valid.",
            fields: [{ field: "email", message: "Not an email" }],
          },
        }),
      ),
    );
    const error = await api.post("/users", {}).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect([apiError.status, apiError.code, apiError.correlationId]).toEqual([
      422,
      "validation_error",
      "abc-123",
    ]);
    expect(fieldErrors(apiError)).toEqual({ email: "Not an email" });
    expect(errorMessage(apiError)).toBe("The request is not valid.");
  });

  it("copes with an error answer that is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502 })));
    const error = (await api.get("/cabinets").catch((caught: unknown) => caught)) as ApiError;
    expect([error.status, error.code]).toEqual([502, "error"]);
    expect(error.message).toContain("502");
  });

  it("signs the user out of the app on 401, except for sign-in calls", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", vi.fn(async () => reply(401, { error: { code: "unauthenticated", message: "no" } })));

    await api.get("/cabinets").catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);

    await api.post("/auth/login", {}, { skipAuthRedirect: true }).catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("explains network failures in plain words", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const error = (await api.get("/cabinets").catch((caught: unknown) => caught)) as ApiError;
    expect(error.code).toBe("network");
    expect(error.message).toMatch(/cannot reach the server/i);
  });

  it("does not turn a cancelled request into an error message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new DOMException("cancelled", "AbortError"))));
    await expect(api.get("/cabinets")).rejects.toMatchObject({ name: "AbortError" });
  });
});
