import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken } from "../api/client";
import { emptyPage, me, mockApi, renderApp, unauthenticated } from "../test-utils";

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  window.localStorage.clear();
});

async function fillSignIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText(/organization/i), "demo");
  await user.type(screen.getByLabelText(/email/i), "alice@example.com");
  await user.type(screen.getByLabelText(/password/i), "correct-horse-battery-9");
  await user.click(screen.getByRole("button", { name: /^sign in$/i }));
}

describe("signing in", () => {
  it("sends what was typed and lands on the files page", async () => {
    const user = userEvent.setup();
    let signedIn = false;
    const { calls } = mockApi({
      "GET /auth/me": () => (signedIn ? me() : unauthenticated),
      "POST /auth/login": () => {
        signedIn = true;
        return { body: { state: "active", must_change_password: false, csrf_token: "csrf-abc" } };
      },
      "GET /cabinets": emptyPage,
    });
    renderApp("/login");
    await fillSignIn(user);

    expect(await screen.findByRole("heading", { name: /cabinets/i })).toBeInTheDocument();
    const login = calls.find((call) => call.url.endsWith("/auth/login"));
    expect(login?.body).toEqual({
      organization: "demo",
      email: "alice@example.com",
      password: "correct-horse-battery-9",
    });
    expect(screen.getByRole("link", { name: "Alice" })).toBeInTheDocument();
  });

  it("remembers the organization for next time", async () => {
    const user = userEvent.setup();
    let signedIn = false;
    mockApi({
      "GET /auth/me": () => (signedIn ? me() : unauthenticated),
      "POST /auth/login": () => {
        signedIn = true;
        return { body: { state: "active", must_change_password: false, csrf_token: "c" } };
      },
      "GET /cabinets": emptyPage,
    });
    renderApp("/login");
    await fillSignIn(user);
    await screen.findByRole("heading", { name: /cabinets/i });
    expect(window.localStorage.getItem("docuflow.organization")).toBe("demo");
  });

  it("shows the server's message when the password is wrong and keeps the form", async () => {
    const user = userEvent.setup();
    mockApi({
      "GET /auth/me": unauthenticated,
      "POST /auth/login": {
        status: 401,
        body: {
          error: { code: "unauthenticated", message: "Incorrect organization, email or password." },
        },
      },
    });
    renderApp("/login");
    await fillSignIn(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Incorrect organization, email or password.",
    );
    expect(screen.getByLabelText(/email/i)).toHaveValue("alice@example.com");
  });

  it("asks for a code after the password when two-step sign-in is on", async () => {
    const user = userEvent.setup();
    let signedIn = false;
    const { calls } = mockApi({
      "GET /auth/me": () => (signedIn ? me() : unauthenticated),
      "POST /auth/login": {
        body: { state: "mfa_pending", must_change_password: false, csrf_token: null },
      },
      "POST /auth/mfa": () => {
        signedIn = true;
        return { body: { state: "active", must_change_password: false, csrf_token: "csrf-abc" } };
      },
      "GET /cabinets": emptyPage,
    });
    renderApp("/login");
    await fillSignIn(user);

    expect(await screen.findByRole("heading", { name: /check your code/i })).toBeInTheDocument();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/^code/i), " 123456 ");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    expect(await screen.findByRole("heading", { name: /cabinets/i })).toBeInTheDocument();
    expect(calls.find((call) => call.url.endsWith("/auth/mfa"))?.body).toEqual({ code: "123456" });
  });

  it("does not let the sign-in button be pressed with a missing field", async () => {
    mockApi({ "GET /auth/me": unauthenticated });
    renderApp("/login");
    expect(await screen.findByRole("button", { name: /^sign in$/i })).toBeDisabled();
  });
});

describe("protected pages", () => {
  it("send visitors to the sign-in page", async () => {
    mockApi({ "GET /auth/me": unauthenticated });
    renderApp("/files");
    expect(await screen.findByRole("heading", { name: /sign in to docuflow/i })).toBeInTheDocument();
  });

  it("make a new user choose a password before anything else", async () => {
    const user = userEvent.setup();
    let changed = false;
    const { calls } = mockApi({
      "GET /auth/me": () => me({ mustChange: !changed }),
      "POST /auth/password": () => {
        changed = true;
        return { status: 204 };
      },
      "GET /cabinets": emptyPage,
    });
    renderApp("/files");

    expect(await screen.findByRole("heading", { name: /choose a new password/i })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();

    await user.type(screen.getByLabelText(/current password/i), "temporary-pass-1");
    await user.type(screen.getByLabelText(/^new password/i), "my-own-strong-pass-5");
    await user.type(screen.getByLabelText(/confirm new password/i), "different-password-1");
    expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /change password/i })).toBeDisabled();

    await user.clear(screen.getByLabelText(/confirm new password/i));
    await user.type(screen.getByLabelText(/confirm new password/i), "my-own-strong-pass-5");
    await user.click(screen.getByRole("button", { name: /change password/i }));

    expect(await screen.findByRole("heading", { name: /cabinets/i })).toBeInTheDocument();
    expect(calls.find((call) => call.url.endsWith("/auth/password"))?.body).toEqual({
      current_password: "temporary-pass-1",
      new_password: "my-own-strong-pass-5",
    });
  });

  it("only offer administration links to people who may use them", async () => {
    mockApi({ "GET /auth/me": () => me({ permissions: [] }), "GET /cabinets": emptyPage });
    renderApp("/files");
    await screen.findByRole("heading", { name: /cabinets/i });
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "API keys" })).not.toBeInTheDocument();
  });

  it("show administration links to administrators", async () => {
    mockApi({
      "GET /auth/me": () =>
        me({ permissions: ["users.manage", "groups.manage", "integrations.manage", "cabinets.manage"] }),
      "GET /cabinets": emptyPage,
    });
    renderApp("/files");
    await waitFor(() => expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "API keys" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new cabinet/i })).toBeInTheDocument();
  });
});
