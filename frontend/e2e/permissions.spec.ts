import { expect, test } from "@playwright/test";

import { newSession, signInAndWait } from "./helpers";

const FELIX = "Felix (felix@example.com)";

test("a permission change applies to the very next page load", async ({ page, browser }) => {
  await signInAndWait(page, "admin@example.com");
  const felix = await newSession(browser, "felix@example.com");
  const financeLink = felix.page.getByRole("link", { name: "Finance", exact: true }).first();
  await expect(financeLink).toBeVisible();

  await page.getByRole("link", { name: "Finance", exact: true }).first().click();
  await page.getByRole("button", { name: "Permissions…" }).click();
  const dialog = page.getByRole("dialog", { name: "Permissions for Finance" });

  // The inspector explains Felix's access before any change.
  await dialog.getByLabel("Person", { exact: true }).selectOption({ label: FELIX });
  const readRow = dialog.getByRole("row", { name: /^read Allowed/ });
  await expect(readRow).toContainText("set on this item");
  await expect(dialog.getByRole("row", { name: /^delete Not allowed/ })).toContainText(
    "no matching entry",
  );

  // Deny Felix the right to read.
  await dialog.getByLabel("Type", { exact: true }).selectOption("user");
  await dialog.getByLabel("Who", { exact: true }).selectOption({ label: FELIX });
  await dialog.getByLabel("Permission", { exact: true }).selectOption("read");
  await dialog.getByLabel("Effect", { exact: true }).selectOption("deny");
  await dialog.getByRole("button", { name: "Add", exact: true }).click();
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(dialog.getByRole("status")).toContainText("The change applies immediately");
  await expect(dialog.getByRole("row", { name: /^read Not allowed/ })).toContainText("denied here");

  // Felix reloads once and the cabinet is gone.
  await felix.page.reload();
  await expect(felix.page.getByText("No cabinets you can see")).toBeVisible();
  await expect(felix.page.getByRole("link", { name: "Finance", exact: true })).toHaveCount(0);

  // Take the deny away again and it comes back.
  await dialog
    .getByRole("row", { name: /Felix.*read.*deny/ })
    .getByRole("button", { name: /Remove/ })
    .click();
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(dialog.getByRole("status")).toBeVisible();
  await felix.page.reload();
  await expect(felix.page.getByRole("link", { name: "Finance", exact: true }).first()).toBeVisible();
  await felix.context.close();
});

test("someone who cannot manage access cannot open the permissions editor", async ({ page }) => {
  await signInAndWait(page, "felix@example.com");
  await page.getByRole("link", { name: "Finance", exact: true }).first().click();
  const permissions = page.getByRole("button", { name: "Permissions…" });
    await expect(permissions).toBeDisabled();
    await expect(permissions).toHaveAttribute("title", "You cannot change who has access here");
});
