import { expect, test } from "@playwright/test";

import { Api, newSession, signInAndWait, signOut } from "./helpers";

test.describe("Finance and HR see only their own filing", () => {
  test("each person sees their department's cabinet and nothing else", async ({ browser }) => {
    const felix = await newSession(browser, "felix@example.com");
    const hana = await newSession(browser, "hana@example.com");

    await expect(felix.page.getByRole("link", { name: "Finance", exact: true }).first()).toBeVisible();
    await expect(felix.page.getByRole("link", { name: "HR", exact: true })).toHaveCount(0);
    await expect(hana.page.getByRole("link", { name: "HR", exact: true }).first()).toBeVisible();
    await expect(hana.page.getByRole("link", { name: "Finance", exact: true })).toHaveCount(0);

    await felix.context.close();
    await hana.context.close();
  });

  test("opening another department's cabinet by its address shows nothing about it", async ({
    browser,
    playwright,
  }) => {
    const hanaApi = await Api.signIn(playwright, "hana@example.com");
    const cabinets = (await (await hanaApi.get("/cabinets")).json()) as {
      items: { id: string; name: string }[];
    };
    const hr = cabinets.items.find((item) => item.name === "HR");
    expect(hr).toBeDefined();
    await hanaApi.dispose();

    const felix = await newSession(browser, "felix@example.com");
    await felix.page.goto(`/files/cabinets/${hr?.id}`);
    await expect(felix.page.getByRole("alert")).toHaveText("Cabinet not found.");
    // The page gives no hint of what the cabinet is called or what it holds.
    await expect(felix.page.getByText("Employee Files")).toHaveCount(0);
    await expect(felix.page.getByText("Contract - Hana.pdf")).toHaveCount(0);
    await felix.context.close();
  });

  test("the tree and counts for Finance show only what Felix may open", async ({ page }) => {
    await signInAndWait(page, "felix@example.com");
    await page.getByRole("button", { name: "Expand Finance" }).click();
    const tree = page.getByRole("list", { name: "Cabinets and folders" });
    await expect(tree.getByRole("link", { name: "Supplier Invoices" })).toBeVisible();
    await tree.getByRole("link", { name: "Finance", exact: true }).click();

    await expect(page.getByRole("heading", { level: 1, name: "Finance" })).toBeVisible();
    // The two seeded invoices are 184,320 and 201,760 bytes.
    const stats = page.getByRole("term").filter({ hasText: "Storage" });
    await expect(stats).toBeVisible();
    await expect(page.getByText("377 KB")).toBeVisible();
    await signOut(page);
  });

  test("buttons Felix may not use are disabled and say why", async ({ page }) => {
    await signInAndWait(page, "felix@example.com");
    await page.getByRole("link", { name: "Finance", exact: true }).first().click();
    await expect(page.getByRole("button", { name: "New folder" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Delete" }).first()).toBeDisabled();
    const permissions = page.getByRole("button", { name: "Permissions…" });
    await expect(permissions).toBeDisabled();
    await expect(permissions).toHaveAttribute("title", "You cannot change who has access here");
  });
});
