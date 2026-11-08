import { describe, expect, it } from "vitest";

import { locationOf, splitSnippet } from "./Search";

describe("splitSnippet", () => {
  it("marks the matched words and keeps the rest", () => {
    expect(splitSnippet("for [[consulting]] services and [[fees]]")).toEqual([
      { text: "for ", match: false },
      { text: "consulting", match: true },
      { text: " services and ", match: false },
      { text: "fees", match: true },
    ]);
  });

  it("treats text with no marks as plain", () => {
    expect(splitSnippet("nothing here")).toEqual([{ text: "nothing here", match: false }]);
  });
});

describe("locationOf", () => {
  it("opens the folder, or the cabinet for a document filed at its top", () => {
    expect(locationOf({ cabinet_id: "c1", folder_id: "f1" })).toBe("/files/folders/f1");
    expect(locationOf({ cabinet_id: "c1", folder_id: null })).toBe("/files/cabinets/c1");
  });
});
