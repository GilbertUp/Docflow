import { expect, test } from "@playwright/test";

import { E2E_PASSWORD, emailFor, signIn, signInAndWait, uniqueName } from "./helpers";

test("a new person chooses a password, sees nothing, then sees what they are given", async ({
  page,
  browser,
}) => {
  const name = uniqueName("Newcomer");
  const email = emailFor(name);
  const temporary = "Temporary-Passw0rd-7";

  await signInAndWait(page, "admin@example.com");
  await page.getByRole("link", { name: "Users", exact: true }).click();
  await page.getByRole("button", { name: "New user" }).click();
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Temporary password").fill(temporary);
  await page.getByRole("button", { name: "Create user" }).click();
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toContainText("Must change password");

  const newcomerContext = await browser.newContext();
  const newcomer = await newcomerContext.newPage();
  await signIn(newcomer, email, temporary);
  await expect(newcomer.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  // Nothing else is reachable until the password is changed.
  await expect(newcomer.getByRole("navigation", { name: "Main" })).toHaveCount(0);
  await newcomer.goto("/files");
  await expect(newcomer.getByRole("heading", { name: "Choose a new password" })).toBeVisible();

  await newcomer.getByLabel("Current password").fill(temporary);
  await newcomer.getByLabel(/^New password/).fill(E2E_PASSWORD);
  await newcomer.getByLabel("Confirm new password").fill(E2E_PASSWORD);
  await newcomer.getByRole("button", { name: "Change password" }).click();
  await expect(newcomer.getByRole("heading", { name: "Cabinets", exact: true })).toBeVisible();
  await expect(newcomer.getByText("No cabinets you can see")).toBeVisible();
  await expect(newcomer.getByRole("link", { name: "Users", exact: true })).toHaveCount(0);

  // An administrator adds them to the Finance team, and the next load shows Finance.
  await page.getByRole("link", { name: "Groups", exact: true }).click();
  await page.getByRole("button", { name: "Finance team" }).click();
  await page.getByLabel("Add a person").selectOption({ label: `${name} (${email})` });
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();

  await newcomer.reload();
  await expect(newcomer.getByRole("link", { name: "Finance", exact: true }).first()).toBeVisible();
  await expect(newcomer.getByRole("link", { name: "HR", exact: true })).toHaveCount(0);

  // Removing them takes it away again.
  await page
    .getByRole("row", { name: new RegExp(name) })
    .getByRole("button", { name: /Remove/ })
    .click();
  await newcomer.reload();
  await expect(newcomer.getByText("No cabinets you can see")).toBeVisible();
  await newcomerContext.close();
});

test("a disabled person is signed out at once and cannot sign back in", async ({
  page,
  browser,
}) => {
  const name = uniqueName("Leaver");
  const email = emailFor(name);
  await signInAndWait(page, "admin@example.com");
  await page.getByRole("link", { name: "Users", exact: true }).click();
  await page.getByRole("button", { name: "New user" }).click();
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Temporary password").fill("Temporary-Passw0rd-7");
  await page.getByRole("button", { name: "Create user" }).click();

  const leaverContext = await browser.newContext();
  const leaver = await leaverContext.newPage();
  await signIn(leaver, email, "Temporary-Passw0rd-7");
  await expect(leaver.getByRole("heading", { name: "Choose a new password" })).toBeVisible();

  const row = page.getByRole("row", { name: new RegExp(name) });
  await row.locator("summary").click();
  await row.getByRole("button", { name: "Disable" }).click();
  await expect(row).toContainText("Disabled");

  await leaver.reload();
  await expect(leaver.getByRole("heading", { name: "Sign in to DocuFlow" })).toBeVisible();
  await signIn(leaver, email, "Temporary-Passw0rd-7");
  await expect(leaver.getByRole("alert")).toHaveText("Incorrect organization, email or password.");
  await leaverContext.close();
});
