import { describe, expect, it } from "vitest";

import { modeLabel, sourceLocation } from "./Ask";

describe("modeLabel", () => {
  it("never lets extracts pass for a written answer", () => {
    expect(modeLabel("extracts")).toMatch(/Nothing has been written/);
    expect(modeLabel("generated")).toMatch(/Written by an AI service/);
    expect(modeLabel("insufficient_evidence")).toBe("Not enough evidence");
  });
});

describe("sourceLocation", () => {
  it("opens the folder, or the cabinet for a document filed at its top", () => {
    expect(sourceLocation({ cabinet_id: "c", folder_id: "f" })).toBe("/files/folders/f");
    expect(sourceLocation({ cabinet_id: "c", folder_id: null })).toBe("/files/cabinets/c");
  });
});
