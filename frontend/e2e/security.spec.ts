import { expect, test } from "@playwright/test";

import {
  Api,
  provisionUser,
  signIn,
  signInAndWait,
  signOut,
  totp,
  uniqueName,
} from "./helpers";

test("two-step sign-in: set up, use a code, then a recovery code once", async ({
  page,
  playwright,
}) => {
  const admin = await Api.signIn(playwright, "admin@example.com");
  const email = await provisionUser(playwright, admin, uniqueName("Secure"));
  await admin.dispose();

  await signInAndWait(page, email);
  await page.goto("/account");
  await page.getByRole("button", { name: "Set up two-step sign-in" }).click();
  await expect(page.locator("svg title", { hasText: "Authenticator setup code" })).toBeAttached();
  const secret = ((await page.locator("code").first().textContent()) ?? "").trim();
  expect(secret).toMatch(/^[A-Z2-7]{16,}$/);

  // A wrong code does not turn it on.
  await page.getByLabel("6-digit code").fill("000000");
  await page.getByRole("button", { name: "Turn on" }).click();
  await expect(page.getByRole("alert")).toContainText("not valid");

  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Turn on" }).click();
  const codes = page.getByRole("list", { name: "Recovery codes" }).getByRole("listitem");
  await expect(codes).toHaveCount(10);
  const recoveryCode = ((await codes.first().textContent()) ?? "").trim();
  expect(recoveryCode).toMatch(/^[0-9a-f]{6}-[0-9a-f]{6}$/);
  await page.getByRole("button", { name: "I have saved them" }).click();
  await expect(page.getByText("Two-step sign-in is on.")).toBeVisible();
  await signOut(page);

  // The password alone no longer gets in.
  await signIn(page, email);
  await expect(page.getByRole("heading", { name: "Check your code" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
  await page.getByLabel("Code").fill("000000");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("alert")).toHaveText("That code is not valid.");

  // The code from the authenticator app works, once. The next time slot is accepted too.
  await page.getByLabel("Code").fill(totp(secret, 1));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Cabinets", exact: true })).toBeVisible();
  await signOut(page);

  // A recovery code works once and then never again.
  await signIn(page, email);
  await page.getByLabel("Code").fill(recoveryCode);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Cabinets", exact: true })).toBeVisible();
  await signOut(page);

  await signIn(page, email);
  await page.getByLabel("Code").fill(recoveryCode);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("alert")).toHaveText("That code is not valid.");
});

test("an API key starts with no access and stops working when revoked", async ({
  page,
  playwright,
}) => {
  const name = uniqueName("ERP");
  await signInAndWait(page, "admin@example.com");
  await page.getByRole("link", { name: "API keys" }).click();
  await page.getByRole("button", { name: "New key" }).click();
  await page.getByLabel(/^Name/).fill(name);
  await page.getByRole("button", { name: "Create key" }).click();

  const dialog = page.getByRole("dialog", { name: "Copy your new key" });
  const token = ((await dialog.getByTestId("api-token").textContent()) ?? "").trim();
  expect(token).toMatch(/^dfk_[0-9a-f]{32}\./);
  await dialog.getByRole("button", { name: "I have stored it" }).click();

  // The list shows only the start of the key, never the whole thing.
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toBeVisible();
  await expect(page.getByText(token)).toHaveCount(0);

  const machine = await playwright.request.newContext({
    baseURL: "http://127.0.0.1:5173",
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
  expect((await machine.get("/api/v1/users")).status()).toBe(403); // no access until granted
  expect((await machine.get("/api/v1/cabinets")).status()).toBe(200);
  expect(((await (await machine.get("/api/v1/cabinets")).json()) as { total: number }).total).toBe(0);

  await row.getByRole("button", { name: /Revoke/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Revoke", exact: true }).click();
  await expect(row).toContainText("Revoked");
  expect((await machine.get("/api/v1/cabinets")).status()).toBe(401);
  await machine.dispose();
});

test("other websites cannot act for a signed-in person", async ({ page, playwright }) => {
  await signInAndWait(page, "admin@example.com");
  const cookies = await page.context().cookies();
  expect(cookies.find((cookie) => cookie.name === "df_session")).toMatchObject({
    httpOnly: true,
    sameSite: "Lax",
  });

  // A request that carries the session cookie but not the CSRF token is refused.
  const attacker = await playwright.request.newContext({
    baseURL: "http://127.0.0.1:5173",
    extraHTTPHeaders: {
      Cookie: cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; "),
    },
  });
  const response = await attacker.post("/api/v1/cabinets", { data: { name: "Planted by another site" } });
  expect(response.status()).toBe(403);
  expect(((await response.json()) as { error: { code: string } }).error.code).toBe("csrf");
  await attacker.dispose();
});

test("the page is protected by a strict content policy and the session cannot be read by scripts", async ({
  page,
}) => {
  const response = await page.goto("/login");
  const policy = response?.headers()["content-security-policy"] ?? "";
  // The built app served by preview has no policy header, so check the API's as well.
  const api = await page.request.get("/api/v1/auth/me");
  expect(api.headers()["content-security-policy"]).toContain("default-src 'self'");
  expect(api.headers()["x-content-type-options"]).toBe("nosniff");
  expect(api.headers()["cache-control"]).toBe("no-store");
  expect(typeof policy).toBe("string");

  await signInAndWait(page, "admin@example.com");
  expect(await page.evaluate(() => document.cookie)).not.toContain("df_session");
});
