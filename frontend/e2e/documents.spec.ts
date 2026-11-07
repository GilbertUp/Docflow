import { type Page, expect, test } from "@playwright/test";

import { newSession, signInAndWait, uniqueName, zipOf } from "./helpers";

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

test.describe("Importing an archive", () => {
  test("a ZIP becomes folders and documents, and the files it could not file are listed", async ({
    page,
  }) => {
    const folder = uniqueName("Inbox");
    await signInAndWait(page, "admin@example.com");
    await openNewFolder(page, folder);

    await page.getByLabel("Choose a ZIP archive to import").setInputFiles({
      name: "bundle.zip",
      mimeType: "application/zip",
      buffer: zipOf({
        "Reports/2026/q1.pdf": PDF(`q1 ${folder}`),
        "Reports/summary.txt": Buffer.from(`summary ${folder}`),
        "notes.txt": Buffer.from(`notes ${folder}`),
        "program.pdf": Buffer.from("MZ not a pdf"),
        "../escape.pdf": PDF(`escape ${folder}`),
      }),
    });

    const panel = page.getByRole("region", { name: /Importing bundle\.zip/ });
    await expect(panel.getByText(/3 of 5 files were filed/)).toBeVisible();
    await expect(panel.getByRole("row", { name: /program\.pdf/ })).toContainText("not accepted");
    await expect(panel.getByRole("row", { name: /escape\.pdf/ })).toContainText("not safe");

    await expect(page.getByRole("link", { name: "Reports" })).toBeVisible();
    await expect(page.getByRole("row", { name: /notes\.txt/ })).toBeVisible();
    await page.getByRole("link", { name: "Reports" }).click();
    await expect(page.getByRole("row", { name: /summary\.txt/ })).toBeVisible();
    await page.getByRole("link", { name: "2026" }).click();
    await expect(page.getByRole("row", { name: /q1\.pdf/ })).toBeVisible();
  });
});

test.describe("Reading the text of documents", () => {
  test("a text file is read, shown page by page, and can be read again in chosen languages", async ({
    page,
  }) => {
    const folder = uniqueName("Memos");
    const sentence = `Payment is due within thirty days of the invoice date ${folder}`;
    await signInAndWait(page, "admin@example.com");
    await openNewFolder(page, folder);
    await page.getByLabel("Choose files to upload").setInputFiles({
      name: "terms.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(sentence),
    });
    await expect(page.getByRole("row", { name: /terms\.txt/ })).toBeVisible();

    await page.getByRole("button", { name: /Text of terms\.txt/ }).click();
    const dialog = page.getByRole("dialog", { name: /Text of terms\.txt/ });
    await expect(dialog.getByText(/Version 1: Text ready/)).toBeVisible();
    await expect(dialog.getByText("From the file")).toBeVisible();
    await expect(dialog.getByText(sentence)).toBeVisible();

    // The language chooser either lists what the server can read, or says it cannot read scans.
    await expect(dialog.getByText(/not set up on this server|Choose up to \d+/)).toBeVisible();
    await dialog.getByRole("button", { name: "Done" }).click();

    await page.getByRole("button", { name: /Versions of terms\.txt/ }).click();
    await expect(
      page.getByRole("dialog", { name: /Versions of terms\.txt/ }).getByText("Text ready"),
    ).toBeVisible();
  });
});

test.describe("Document types", () => {
  test("an administrator makes a type, a document is filled in, and mistakes are shown together", async ({
    page,
  }) => {
    const typeName = uniqueName("Invoice type");
    const folder = uniqueName("Typed");
    await signInAndWait(page, "admin@example.com");

    await page.getByRole("link", { name: "Document types", exact: true }).click();
    await page.getByRole("button", { name: "New document type" }).click();
    const editor = page.getByRole("dialog", { name: "New document type" });
    await editor.getByRole("textbox", { name: /^Name/ }).fill(typeName);
    await editor.getByLabel("Label (field 1)").fill("Invoice number");
    await expect(editor.getByLabel("Key (field 1)")).toHaveValue("invoice_number");
    await editor.getByLabel("Required (field 1)").check();
    await editor.getByLabel("Must match pattern (field 1)").fill(String.raw`INV-\d{4}`);
    await editor.getByRole("button", { name: "Add a field" }).click();
    await editor.getByLabel("Label (field 2)").fill("Total");
    await editor.getByLabel("Type (field 2)").selectOption("number");
    await editor.getByLabel("Required (field 2)").check();
    await editor.getByLabel("Lowest allowed (field 2)").fill("0");
    await editor.getByRole("button", { name: "Create type" }).click();
    const row = page.getByRole("row", { name: new RegExp(typeName) });
    await expect(row).toContainText("Invoice number, Total");

    await page.getByRole("link", { name: "Files", exact: true }).click();
    await openNewFolder(page, folder);
    await page.getByLabel("Choose files to upload").setInputFiles({
      name: "invoice.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(`invoice ${folder}`),
    });
    await expect(page.getByRole("row", { name: /invoice\.txt/ })).toBeVisible();

    await page.getByRole("button", { name: /Details of invoice\.txt/ }).click();
    const details = page.getByRole("dialog", { name: /Details of invoice\.txt/ });
    await details.getByLabel("Document type").selectOption({ label: typeName });
    await details.getByLabel(/Invoice number/).fill("not valid");
    await details.getByLabel(/Total/).fill("-5");
    await details.getByRole("button", { name: "Save" }).click();
    await expect(details.getByText(/does not match the expected format/)).toBeVisible();
    await expect(details.getByText(/Use 0 or more/)).toBeVisible();

    await details.getByLabel(/Invoice number/).fill("INV-1042");
    await details.getByLabel(/Total/).fill("1042.50");
    await details.getByRole("button", { name: "Save" }).click();
    await expect(details.getByText("Saved.")).toBeVisible();
    await details.getByRole("button", { name: "Cancel" }).click();

    await expect(page.getByRole("row", { name: /invoice\.txt/ })).toContainText(typeName);
    await page.getByRole("button", { name: /Details of invoice\.txt/ }).click();
    const again = page.getByRole("dialog", { name: /Details of invoice\.txt/ });
    await expect(again.getByLabel(/Invoice number/)).toHaveValue("INV-1042");
    await expect(again.getByLabel(/Total/)).toHaveValue("1042.5");
  });

  test("changing a type's fields publishes a new version, and an ordinary person cannot", async ({
    page,
    browser,
  }) => {
    const typeName = uniqueName("Versioned type");
    await signInAndWait(page, "admin@example.com");
    await page.getByRole("link", { name: "Document types", exact: true }).click();
    await page.getByRole("button", { name: "New document type" }).click();
    const editor = page.getByRole("dialog", { name: "New document type" });
    await editor.getByRole("textbox", { name: /^Name/ }).fill(typeName);
    await editor.getByLabel("Label (field 1)").fill("Reference");
    await editor.getByRole("button", { name: "Create type" }).click();
    const row = page.getByRole("row", { name: new RegExp(typeName) });
    await expect(row).toBeVisible();

    await row.locator("summary").click();
    await row.getByRole("button", { name: "Change fields…", exact: true }).click();
    const change = page.getByRole("dialog", { name: `Fields of ${typeName}` });
    await expect(change.getByText(/publishes version 2/)).toBeVisible();
    await change.getByRole("button", { name: "Add a field" }).click();
    await change.getByLabel("Label (field 2)").fill("Approved by");
    await change.getByRole("button", { name: "Publish new version" }).click();
    await expect(page.getByRole("row", { name: new RegExp(typeName) })).toContainText(
      "Reference, Approved by",
    );

    const felix = await newSession(browser, "felix@example.com");
    await expect(felix.page.getByRole("link", { name: "Document types" })).toHaveCount(0);
    await felix.context.close();
  });
});
