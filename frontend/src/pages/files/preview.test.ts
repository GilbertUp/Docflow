import { describe, expect, it } from "vitest";

import { clampPage, pageUrl } from "./PreviewDialog";

describe("pageUrl", () => {
  it("asks the server for a rendered page, never the file", () => {
    expect(pageUrl("abc", 3, 800)).toBe("/api/v1/documents/abc/preview/pages/3?width=800");
  });
});

describe("clampPage", () => {
  it("keeps the page number inside the pages that exist", () => {
    expect(clampPage(0, 5)).toBe(1);
    expect(clampPage(9, 5)).toBe(5);
    expect(clampPage(3, 5)).toBe(3);
    expect(clampPage(2, 0)).toBe(1);
  });
});
