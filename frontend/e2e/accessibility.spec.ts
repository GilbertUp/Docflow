import AxeBuilder from "@axe-core/playwright";
import { type Page, expect, test } from "@playwright/test";

import { signInAndWait } from "./helpers";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function scan(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const problems = results.violations.map(
    (violation) =>
      `${violation.id} (${violation.impact}): ${violation.help} — ${violation.nodes
        .slice(0, 3)
        .map((node) => node.target.join(" "))
        .join(" | ")}`,
  );
  expect(problems, `${label}: accessibility problems`).toEqual([]);
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`accessibility in ${scheme} mode`, () => {
    test.beforeEach(async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
    });

    test("sign-in page", async ({ page }) => {
      await page.goto("/login");
      await expect(page.getByRole("heading", { name: "Sign in to DocuFlow" })).toBeVisible();
      await scan(page, "sign-in");
    });

    test("cabinets, a cabinet and its dialogs", async ({ page }) => {
      await signInAndWait(page, "admin@example.com");
      await scan(page, "cabinets");

      await page.getByRole("link", { name: "Finance", exact: true }).first().click();
      await expect(page.getByRole("heading", { level: 1, name: "Finance" })).toBeVisible();
      await scan(page, "cabinet");

      await page.getByRole("button", { name: "New folder" }).click();
      await expect(page.getByRole("dialog", { name: /New folder/ })).toBeVisible();
      await scan(page, "new folder dialog");
      await page.getByRole("button", { name: "Close" }).click();

      await page.getByRole("button", { name: "Permissions…" }).click();
      await expect(page.getByRole("dialog", { name: /Permissions for/ })).toBeVisible();
      await expect(page.getByRole("dialog").getByRole("table").first()).toBeVisible();
      await scan(page, "permissions dialog");
    });

    test("folder with documents", async ({ page }) => {
      await signInAndWait(page, "admin@example.com");
      await page.getByRole("button", { name: "Expand Finance" }).click();
      await page.getByRole("link", { name: "Supplier Invoices" }).click();
      await page.getByRole("link", { name: "2026" }).click();
      await page.getByRole("link", { name: "November" }).click();
      await expect(page.getByRole("cell", { name: "Invoice ABC-1042.pdf", exact: true })).toBeVisible();
      await scan(page, "folder with documents");

      await page.getByRole("button", { name: /Versions of Invoice ABC-1042\.pdf/ }).click();
      await expect(page.getByRole("dialog", { name: /Versions of/ })).toBeVisible();
      await expect(page.getByRole("dialog").getByRole("table")).toBeVisible();
      await scan(page, "versions dialog");
    });

    test("administration pages and the account page", async ({ page }) => {
      await signInAndWait(page, "admin@example.com");
      for (const [link, heading] of [
        ["Users", "Users"],
        ["Groups", "Groups"],
        ["Roles", "Roles"],
        ["API keys", "API keys"],
      ] as const) {
        await page.getByRole("link", { name: link, exact: true }).click();
        await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
        await expect(page.getByText("Loading")).toHaveCount(0);
        await scan(page, link);
      }
      await page.goto("/account");
      await expect(page.getByRole("heading", { level: 1, name: "Your account" })).toBeVisible();
      await expect(page.getByText("Loading")).toHaveCount(0);
      await scan(page, "account");
    });
  });
}

test("keyboard users can sign in and reach the main content without a mouse", async ({ page }) => {
  await page.goto("/login");
  // Wait for the form: the page first shows "Checking your session" and has nothing to focus.
  await expect(page.getByRole("heading", { name: "Sign in to DocuFlow" })).toBeVisible();
  await page.keyboard.press("Tab");
  await page.keyboard.type("demo");
  await page.keyboard.press("Tab");
  await page.keyboard.type("felix@example.com");
  await page.keyboard.press("Tab");
  await page.keyboard.type("e2e-Passw0rd-12345");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Cabinets", exact: true })).toBeVisible();

  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
});

test("the page fits a phone without sideways scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await signInAndWait(page, "admin@example.com");
  await page.getByRole("link", { name: "Finance", exact: true }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: "Finance" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
