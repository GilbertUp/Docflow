import { createHmac } from "node:crypto";

import {
  type APIRequestContext,
  type Browser,
  type BrowserContext,
  type Page,
  type PlaywrightWorkerArgs,
  expect,
} from "@playwright/test";

import { E2E_PASSWORD, ORGANIZATION } from "./constants";

export { E2E_PASSWORD, ORGANIZATION };

export function uniqueName(prefix: string): string {
  return `${prefix} ${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
}

function base32Decode(input: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const bytes: number[] = [];
  let bits = 0;
  let value = 0;
  for (const char of input.replace(/=+$/, "").toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index < 0) continue;
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 255);
      bits -= 8;
      value &= (1 << bits) - 1;
    }
  }
  return Buffer.from(bytes);
}

/** A time-based one-time code (RFC 6238), the same thing an authenticator app computes. */
export function totp(secret: string, stepOffset = 0, atMs = Date.now()): string {
  const counter = Math.floor(atMs / 1000 / 30) + stepOffset;
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const digest = createHmac("sha1", base32Decode(secret)).update(message).digest();
  const offset = (digest[digest.length - 1] ?? 0) & 0xf;
  const code =
    (((digest[offset] ?? 0) & 0x7f) << 24) |
    (((digest[offset + 1] ?? 0) & 0xff) << 16) |
    (((digest[offset + 2] ?? 0) & 0xff) << 8) |
    ((digest[offset + 3] ?? 0) & 0xff);
  return String(code % 1_000_000).padStart(6, "0");
}

export async function signIn(page: Page, email: string, password = E2E_PASSWORD): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Organization").fill(ORGANIZATION);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

export async function signInAndWait(page: Page, email: string, password = E2E_PASSWORD) {
  await signIn(page, email, password);
  await expect(page.getByRole("heading", { name: "Cabinets", exact: true })).toBeVisible();
}

/** A separate browser profile, like a second person on a second computer. */
export async function newSession(
  browser: Browser,
  email: string,
  password = E2E_PASSWORD,
): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signInAndWait(page, email, password);
  return { context, page };
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole("banner").getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to DocuFlow" })).toBeVisible();
}

/** Calls the API the way the web client does, with the session cookie and CSRF token. */
export class Api {
  private constructor(
    readonly context: APIRequestContext,
    private readonly csrf: string,
  ) {}

  static async signIn(
    playwright: PlaywrightWorkerArgs["playwright"],
    email: string,
    password = E2E_PASSWORD,
  ): Promise<Api> {
    const context = await playwright.request.newContext({ baseURL: "http://127.0.0.1:5173" });
    const response = await context.post("/api/v1/auth/login", {
      data: { organization: ORGANIZATION, email, password },
    });
    expect(response.status(), await response.text()).toBe(200);
    const body = (await response.json()) as { csrf_token: string | null };
    return new Api(context, body.csrf_token ?? "");
  }

  get(path: string) {
    return this.context.get(`/api/v1${path}`);
  }

  post(path: string, data?: unknown) {
    return this.context.post(`/api/v1${path}`, { data, headers: { "X-CSRF-Token": this.csrf } });
  }

  async dispose() {
    await this.context.dispose();
  }
}

/** Creates a person who has already chosen their own password, so tests can sign in directly. */
export function emailFor(name: string): string {
  return `${name.toLowerCase().replace(/[^a-z0-9]+/g, ".")}@example.com`;
}

export async function provisionUser(
  playwright: PlaywrightWorkerArgs["playwright"],
  admin: Api,
  name: string,
): Promise<string> {
  const email = emailFor(name);
  const temporary = "temporary-Passw0rd-1";
  const created = await admin.post("/users", { email, display_name: name, password: temporary });
  expect(created.status(), await created.text()).toBe(201);
  const user = await Api.signIn(playwright, email, temporary);
  const changed = await user.post("/auth/password", {
    current_password: temporary,
    new_password: E2E_PASSWORD,
  });
  expect(changed.status(), await changed.text()).toBe(204);
  await user.dispose();
  return email;
}

/** The table row whose name link is exactly this, so "Report" does not match "Report (copy)". */
export function folderRow(page: Page, name: string) {
  return page.getByRole("row").filter({ has: page.getByRole("link", { name, exact: true }) });
}

export async function openRowAction(page: Page, name: string, action: string) {
  const row = folderRow(page, name);
  await row.locator("summary").click();
  await row.getByRole("button", { name: action, exact: true }).click();
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) crc = CRC_TABLE[(crc ^ byte) & 0xff]! ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** A ZIP archive with stored (uncompressed) entries, built without a library. */
export function zipOf(entries: Record<string, Buffer>): Buffer {
  const parts: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;
  for (const [name, data] of Object.entries(entries)) {
    const nameBytes = Buffer.from(name, "utf8");
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6); // names are UTF-8
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    parts.push(local, nameBytes, data);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x0800, 8);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(data.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(nameBytes.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, nameBytes);
    offset += local.length + nameBytes.length + data.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(entries).length, 8);
  end.writeUInt16LE(Object.keys(entries).length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, directory, end]);
}
