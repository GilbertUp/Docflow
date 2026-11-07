import { expect, test } from "@playwright/test";

import { newSession, signInAndWait, uniqueName } from "./helpers";

const PDF = (text: string) =>
  Buffer.from(`%PDF-1.7\n1 0 obj<<>>endobj\n${text}\n%%EOF`, "utf8");

test.describe("Filing documents", () => {
  test("upload, see it listed, download the same bytes, and spot a repeat upload", async ({
    page,
  }) => {
    const folder = uniqueName("Invoices");
    const body = PDF(`supplier ABC ${folder}`);
    await signInAndWait(page, "admin@example.com");
    await page.getByRole("link", { name: "Finance", exact: true }).first().click();
    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill(folder);
    await page.getByRole("button", { name: "Create folder" }).click();
    await page.getByRole("link", { name: folder }).first().click();
    await expect(page.getByText("No documents here")).toBeVisible();

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
    await page.getByRole("link", { name: "Finance", exact: true }).first().click();
    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill(folder);
    await page.getByRole("button", { name: "Create folder" }).click();
    await page.getByRole("link", { name: folder }).first().click();

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
    await page.getByRole("link", { name: "Finance", exact: true }).first().click();
    await page.getByRole("button", { name: "New folder" }).click();
    await page.getByLabel("Folder name").fill(folder);
    await page.getByRole("button", { name: "Create folder" }).click();
    await page.getByRole("link", { name: folder }).first().click();
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
