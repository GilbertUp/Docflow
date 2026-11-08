import { describe, expect, it } from "vitest";

import { pageUrl, shareOf } from "./files/PreviewDialog";
import { signatureStanding } from "./files/SignDialog";

describe("signatureStanding", () => {
  it("says what a signature is worth now", () => {
    expect(signatureStanding({ status: "revoked", current: false, intact: true })).toBe("Revoked");
    expect(signatureStanding({ status: "valid", current: true, intact: true })).toBe(
      "Valid for the current version",
    );
    expect(signatureStanding({ status: "valid", current: false, intact: true })).toBe(
      "Signed an earlier version",
    );
    expect(signatureStanding({ status: "valid", current: true, intact: false })).toMatch(/no longer/);
  });
});

describe("shareOf", () => {
  const box = { left: 100, top: 50, width: 400, height: 200 };
  it("turns a click into shares of the page", () => {
    expect(shareOf({ x: 300, y: 150 }, box)).toEqual({ x: 0.5, y: 0.5 });
  });
  it("keeps a click on the edge inside the page", () => {
    expect(shareOf({ x: 0, y: 900 }, box)).toEqual({ x: 0, y: 1 });
  });
});

describe("pageUrl", () => {
  it("adds a nonce only when asked, so a changed page is fetched again", () => {
    expect(pageUrl("d", 2, 800)).toBe("/api/v1/documents/d/preview/pages/2?width=800");
    expect(pageUrl("d", 2, 800, 3)).toBe("/api/v1/documents/d/preview/pages/2?width=800&v=3");
  });
});
