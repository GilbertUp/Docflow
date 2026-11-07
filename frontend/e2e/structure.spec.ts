import { expect, test } from "@playwright/test";

import { folderRow, openRowAction, signInAndWait, uniqueName } from "./helpers";

test.describe("Building and reorganizing the filing structure", () => {
  test("an administrator builds nested folders, with breadcrumbs", async ({ page }) => {
    const root = uniqueName("Ledger");
    const year = uniqueName("Year");
    const month = uniqueName("Month");
    await signInAndWait(page, "admin@example.com");
    await page.getByRole("link", { name: "Finance", exact: true }).first().click();

    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill(root);
    await page.getByRole("button", { name: "Create folder" }).click();
    await expect(folderRow(page, root)).toBeVisible();

    await page.getByRole("link", { name: root }).first().click();
    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill(year);
    await page.getByRole("button", { name: "Create folder" }).click();
    await page.getByRole("link", { name: year }).first().click();
    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill(month);
    await page.getByRole("button", { name: "Create folder" }).click();
    await page.getByRole("link", { name: month }).first().click();

    const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
    for (const label of ["All cabinets", "Finance", root, year, month]) {
      await expect(crumbs.getByText(label, { exact: true })).toBeVisible();
    }
    await crumbs.getByRole("link", { name: root }).click();
    await expect(page.getByRole("heading", { level: 1, name: root })).toBeVisible();
  });

  test("duplicate names are refused with a clear message", async ({ page }) => {
    const name = uniqueName("Unique");
    await signInAndWait(page, "admin@example.com");
    await page.getByRole("link", { name: "Finance", exact: true }).first().click();
    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill(name);
    await page.getByRole("button", { name: "Create folder" }).click();
    await expect(folderRow(page, name)).toBeVisible();

    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill(name.toUpperCase());
    await page.getByRole("button", { name: "Create folder" }).click();
    await expect(page.getByRole("dialog").getByRole("alert")).toHaveText(
      "A folder with that name already exists here.",
    );
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill("a/b");
    await page.getByRole("button", { name: "Create folder" }).click();
    await expect(page.getByRole("dialog").getByRole("alert")).toContainText("slashes");
  });

  test("rename, move to another cabinet, copy, archive, restore and delete", async ({ page }) => {
    const original = uniqueName("Draft");
    const renamed = uniqueName("Final");
    await signInAndWait(page, "admin@example.com");
    await page.getByRole("link", { name: "Finance", exact: true }).first().click();
    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill(original);
    await page.getByRole("button", { name: "Create folder" }).click();

    await openRowAction(page, original, "Rename");
    await page.getByLabel(/^Name/).fill(renamed);
    await page.getByRole("dialog").getByRole("button", { name: "Rename", exact: true }).click();
    await expect(folderRow(page, renamed)).toBeVisible();
    await expect(folderRow(page, original)).toHaveCount(0);

    await openRowAction(page, renamed, "Copy…");
    const copyDialog = page.getByRole("dialog");
    await copyDialog.getByRole("button", { name: "Finance", exact: true }).click();
    await copyDialog.getByRole("button", { name: "Copy to Finance" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Copied 1 folder." })).toBeVisible();
    // Copying into the same place picks a free name instead of failing.
    await expect(folderRow(page, `${renamed} (copy)`)).toBeVisible();

    await openRowAction(page, renamed, "Move…");
    const moveDialog = page.getByRole("dialog");
    await moveDialog.getByRole("button", { name: "HR", exact: true }).click();
    await moveDialog.getByRole("button", { name: "Move to HR" }).click();
    await expect(page.getByRole("status").filter({ hasText: `Moved “${renamed}”.` })).toBeVisible();
    await expect(folderRow(page, renamed)).toHaveCount(0);

    await page.getByRole("link", { name: "HR", exact: true }).first().click();
    await expect(folderRow(page, renamed)).toBeVisible();
    await expect(page.getByRole("link", { name: `${renamed} (copy)` })).toHaveCount(0);

    await openRowAction(page, renamed, "Archive");
    await expect(folderRow(page, renamed)).toHaveCount(0);
    await page.getByLabel("Show archived").check();
    await expect(folderRow(page, renamed)).toContainText("Archived");
    await openRowAction(page, renamed, "Restore");
    await page.getByLabel("Show archived").uncheck();
    await expect(folderRow(page, renamed)).toBeVisible();

    await openRowAction(page, renamed, "Delete");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect(folderRow(page, renamed)).toHaveCount(0);
  });

  test("a folder cannot be moved into itself, and a full folder cannot be deleted", async ({
    page,
  }) => {
    await signInAndWait(page, "admin@example.com");
    await page.getByRole("link", { name: "Finance", exact: true }).first().click();

    await openRowAction(page, "Supplier Invoices", "Move…");
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Expand Finance" }).click();
    await expect(dialog.getByRole("button", { name: "Supplier Invoices", exact: true })).toBeDisabled();
    await dialog.getByRole("button", { name: "Close" }).click();

    await openRowAction(page, "Supplier Invoices", "Delete");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect(page.getByRole("dialog").getByRole("alert")).toContainText(
      "Only an empty folder can be deleted",
    );
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    await expect(folderRow(page, "Supplier Invoices")).toBeVisible();
  });

  test("new cabinets belong to their creator until access is shared", async ({ page, browser }) => {
    const name = uniqueName("Legal");
    await signInAndWait(page, "admin@example.com");
    await page.getByRole("button", { name: "New cabinet" }).click();
    await page.getByLabel("Cabinet name").fill(name);
    await page.getByRole("button", { name: "Create cabinet" }).click();
    await expect(page.getByRole("link", { name: name }).first()).toBeVisible();

    const felix = await browser.newContext();
    const felixPage = await felix.newPage();
    await signInAndWait(felixPage, "felix@example.com");
    await expect(felixPage.getByRole("link", { name })).toHaveCount(0);
    await felix.close();
  });
});
