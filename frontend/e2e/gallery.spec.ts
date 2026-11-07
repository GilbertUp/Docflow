import { type Page, test } from "@playwright/test";

import { signInAndWait } from "./helpers";

// Screenshots of the main screens for a human to look at. Run with E2E_GALLERY=1.
test.skip(!process.env.E2E_GALLERY, "set E2E_GALLERY=1 to capture screenshots");

const OUT = "e2e-results/gallery";

async function shoot(page: Page, name: string) {
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
}

for (const [scheme, width, height] of [
  ["light", 1280, 900],
  ["dark", 1280, 900],
  ["light", 390, 844],
] as const) {
  test(`screens, ${scheme} ${width}px`, async ({ page }) => {
    const tag = `${scheme}-${width}`;
    await page.emulateMedia({ colorScheme: scheme });
    await page.setViewportSize({ width, height });

    await page.goto("/login");
    await shoot(page, `${tag}-1-sign-in`);

    await signInAndWait(page, "admin@example.com");
    await shoot(page, `${tag}-2-cabinets`);

    await page.getByRole("button", { name: "Expand Finance" }).click();
    await page.getByRole("link", { name: "Supplier Invoices" }).click();
    await page.getByRole("link", { name: "2026" }).click();
    await page.getByRole("link", { name: "November" }).click();
    await page.getByText("Invoice ABC-1042.pdf").waitFor();
    await shoot(page, `${tag}-3-folder`);

    await page.getByRole("button", { name: "Permissions…" }).click();
    await page.getByRole("heading", { name: "Add an entry" }).waitFor();
    await shoot(page, `${tag}-4-permissions`);
    await page.getByRole("button", { name: "Close" }).click();

    await page.getByRole("link", { name: "Users", exact: true }).click();
    await page.getByText("felix@example.com").waitFor();
    await shoot(page, `${tag}-5-users`);

    await page.getByRole("link", { name: "Roles", exact: true }).click();
    await page.getByText("Security admin").first().waitFor();
    await shoot(page, `${tag}-6-roles`);

    await page.goto("/account");
    await page.getByRole("heading", { name: "Where you are signed in" }).waitFor();
    await shoot(page, `${tag}-7-account`);
  });
}
