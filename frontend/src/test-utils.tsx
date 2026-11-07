import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { App } from "./App";
import { AuthProvider } from "./auth/AuthContext";

export interface Reply {
  status?: number;
  body?: unknown;
}

type Handler = Reply | ((request: { url: string; body: unknown }) => Reply);

/**
 * Replaces fetch with a table of "METHOD /path" answers. Any request without an entry fails the
 * test, so a screen cannot quietly call an endpoint the test does not know about.
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: { method: string; url: string; body: unknown; headers: Headers }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.split("?")[0] ?? url;
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body, headers: new Headers(init?.headers) });
    const handler = routes[`${method} ${path.replace("/api/v1", "")}`];
    if (!handler) throw new Error(`Unexpected request: ${method} ${url}`);
    const reply = typeof handler === "function" ? handler({ url, body }) : handler;
    const status = reply.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(reply.body ?? {}), {
      status,
      headers: { "Content-Type": "application/json", "X-Correlation-ID": "test-correlation" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
}

export function renderApp(route = "/login") {
  return render(
    <QueryClientProvider client={newClient()}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export function renderWithQuery(ui: ReactElement) {
  return render(
    <QueryClientProvider client={newClient()}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

export const unauthenticated: Reply = {
  status: 401,
  body: { error: { code: "unauthenticated", message: "Please sign in." }, correlation_id: "x" },
};

export function me(overrides: { permissions?: string[]; mustChange?: boolean } = {}): Reply {
  return {
    body: {
      user: {
        id: "u1",
        email: "alice@example.com",
        display_name: "Alice",
        kind: "person",
        status: "active",
        department_id: null,
        mfa_enabled: false,
        must_change_password: overrides.mustChange ?? false,
        last_login_at: null,
        row_version: 1,
        created_at: "2026-11-07T10:00:00Z",
      },
      system_permissions: overrides.permissions ?? [],
      csrf_token: "csrf-abc",
      auth_method: "session",
    },
  };
}

export const emptyPage = { body: { items: [], total: 0, limit: 200, offset: 0 } };
