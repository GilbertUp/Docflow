import { describe, expect, it } from "vitest";

import { toFormField } from "./admin/FormsAdmin";
import { answersOf, startingValues } from "./Forms";

describe("answersOf", () => {
  it("sends only what was filled in", () => {
    expect(answersOf({ a: "1", b: "", c: "  ", d: "yes" })).toEqual({ a: "1", d: "yes" });
  });
});

describe("startingValues", () => {
  it("starts each field at its default", () => {
    const fields = [
      { key: "a", label: "A", data_type: "text", default_value: "x" },
      { key: "b", label: "B", data_type: "number", default_value: null },
    ] as Parameters<typeof startingValues>[0];
    expect(startingValues(fields)).toEqual({ a: "x", b: "" });
  });
});

describe("toFormField", () => {
  it("leaves out what only document types use", () => {
    const field = toFormField({
      id: 1,
      key: "days",
      keyEdited: true,
      label: "Days",
      data_type: "number",
      required: true,
      default_value: "",
      pattern: "",
      min_length: "",
      max_length: "",
      min: "1",
      max: "",
      choices: "",
      labels: "Days",
      extract_pattern: "",
    });
    expect(field).toEqual({
      key: "days",
      label: "Days",
      data_type: "number",
      required: true,
      default_value: null,
      validation: { min: "1" },
      choices: [],
    });
  });
});
