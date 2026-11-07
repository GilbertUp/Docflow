import { type Page, expect, test } from "@playwright/test";

import { newSession, signInAndWait, uniqueName } from "./helpers";

const PDF = (text: string) =>
  Buffer.from(`%PDF-1.7\n1 0 obj<<>>endobj\n${text}\n%%EOF`, "utf8");

/**
 * Makes a folder in Finance and opens it. Waits until the new folder's own page is showing, so a
 * file chosen next goes to it and not to the page that is being replaced.
 */
async function openNewFolder(page: Page, name: string) {
  await page.getByRole("link", { name: "Finance", exact: true }).first().click();
  await page.getByRole("button", { name: "New folder" }).click();
  await page.getByLabel("Folder name").fill(name);
  await page.getByRole("button", { name: "Create folder" }).click();
  await page.getByRole("link", { name }).first().click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await expect(page.getByText("No documents here")).toBeVisible();
}

test.describe("Filing documents", () => {
  test("upload, see it listed, download the same bytes, and spot a repeat upload", async ({
    page,
  }) => {
    const folder = uniqueName("Invoices");
    const body = PDF(`supplier ABC ${folder}`);
    await signInAndWait(page, "admin@example.com");
    await openNewFolder(page, folder);

    await page.getByLabel("Choose files to upload").setInputFiles({
      name: "ABC invoice.pdf",
      mimeType: "application/pdf",
      buffer: body,
    });
    await expect(page.getByText("ABC invoice.pdf was filed.", { exact: true })).toBeVisible();
    const row = page.getByRole("row", { name: /ABC invoice\.pdf/ });
    await expect(row).toBeVisible();

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      row.getByRole("link", { name: /Download/ }).click(),
    ]);
    expect(download.suggestedFilename()).toBe("ABC invoice.pdf");
    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(chunk as Buffer);
    expect(Buffer.concat(chunks).equals(body)).toBe(true);

    await page.getByLabel("Choose files to upload").setInputFiles({
      name: "copy of invoice.pdf",
      mimeType: "application/pdf",
      buffer: body,
    });
    await expect(page.getByText(/already stored, so no extra space is used/)).toBeVisible();
    await expect(page.getByRole("row", { name: /copy of invoice\.pdf/ })).toBeVisible();
  });

  test("a file that is not what its name says is refused with a clear message", async ({
    page,
  }) => {
    const folder = uniqueName("Refusals");
    await signInAndWait(page, "admin@example.com");
    await openNewFolder(page, folder);

    await page.getByLabel("Choose files to upload").setInputFiles({
      name: "invoice.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("MZ not a pdf"),
    });
    await expect(page.getByRole("alert")).toContainText("invoice.pdf was not filed");
    await expect(page.getByRole("alert")).toContainText("That kind of file is not accepted");
    await expect(page.getByText("No documents here")).toBeVisible();
  });

  test("someone from another department cannot download it", async ({ page, browser }) => {
    const folder = uniqueName("Private");
    await signInAndWait(page, "admin@example.com");
    await openNewFolder(page, folder);
    await page.getByLabel("Choose files to upload").setInputFiles({
      name: "payroll-note.pdf",
      mimeType: "application/pdf",
      buffer: PDF(`confidential ${folder}`),
    });
    const link = page.getByRole("row", { name: /payroll-note\.pdf/ }).getByRole("link", {
      name: /Download/,
    });
    const href = await link.getAttribute("href");
    expect(href).toMatch(/^\/api\/v1\/documents\/[0-9a-f-]+\/download$/);

    const hana = await newSession(browser, "hana@example.com");
    const response = await hana.page.request.get(href as string);
    expect(response.status()).toBe(404);
    await hana.context.close();
  });
});

test.describe("Document versions", () => {
  test("add a version, see the history, and fetch the first file again", async ({ page }) => {
    const folder = uniqueName("Contracts");
    const first = PDF(`terms net 30 ${folder}`);
    const second = PDF(`terms net 45 ${folder}`);
    await signInAndWait(page, "admin@example.com");
    await openNewFolder(page, folder);
    await page.getByLabel("Choose files to upload").setInputFiles({
      name: "contract.pdf",
      mimeType: "application/pdf",
      buffer: first,
    });
    await expect(page.getByRole("row", { name: /contract\.pdf/ })).toBeVisible();

    await page.getByRole("button", { name: /Versions of contract\.pdf/ }).click();
    const dialog = page.getByRole("dialog", { name: /Versions of contract\.pdf/ });
    await expect(dialog.getByRole("row", { name: /contract\.pdf/ })).toHaveCount(1);

    await dialog.getByLabel("New version file").setInputFiles({
      name: "contract signed.pdf",
      mimeType: "application/pdf",
      buffer: second,
    });
    await dialog.getByLabel("What changed (optional)").fill("Payment terms extended");
    await dialog.getByRole("button", { name: "Add version" }).click();
    await expect(dialog.getByText("Version 2 was added.")).toBeVisible();

    const newest = dialog.getByRole("row", { name: /contract signed\.pdf/ });
    await expect(newest).toContainText("Current");
    await expect(newest).toContainText("Payment terms extended");

    const [oldFile] = await Promise.all([
      page.waitForEvent("download"),
      dialog.getByRole("link", { name: "Download version 1" }).click(),
    ]);
    expect(oldFile.suggestedFilename()).toBe("contract.pdf");
    const chunks: Buffer[] = [];
    for await (const chunk of await oldFile.createReadStream()) chunks.push(chunk as Buffer);
    expect(Buffer.concat(chunks).equals(first)).toBe(true);

    await dialog.getByRole("button", { name: "Done" }).click();
    const [current] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("row", { name: /contract\.pdf/ }).getByRole("link", { name: /Download/ }).click(),
    ]);
    const now: Buffer[] = [];
    for await (const chunk of await current.createReadStream()) now.push(chunk as Buffer);
    expect(Buffer.concat(now).equals(second)).toBe(true);
  });
});
