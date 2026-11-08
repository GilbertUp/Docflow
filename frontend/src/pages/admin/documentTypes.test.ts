import { describe, expect, it } from "vitest";

import { keyFromLabel, parseChoices, parseLabels, toDefinition } from "./DocumentTypes";

describe("keyFromLabel", () => {
  it("makes a lowercase key from a label", () => {
    expect(keyFromLabel("Invoice number")).toBe("invoice_number");
    expect(keyFromLabel("  Due date (UTC)! ")).toBe("due_date_utc");
  });

  it("drops accents and starts with a letter", () => {
    expect(keyFromLabel("Numéro de société")).toBe("numero_de_societe");
    expect(keyFromLabel("2026 total")).toBe("f_2026_total");
  });

  it("gives nothing for a label with nothing usable, and keeps keys short", () => {
    expect(keyFromLabel("???")).toBe("");
    expect(keyFromLabel("a".repeat(80))).toHaveLength(40);
  });
});

describe("parseChoices", () => {
  it("reads one choice per line, with an optional label after a bar", () => {
    expect(parseChoices("open\npaid | Paid in full\n\n  closed  ")).toEqual([
      { value: "open", label: "open" },
      { value: "paid", label: "Paid in full" },
      { value: "closed", label: "closed" },
    ]);
  });
});

describe("toDefinition", () => {
  const draft = {
    id: 1,
    key: " total ",
    keyEdited: true,
    label: " Total ",
    data_type: "number" as const,
    required: true,
    default_value: "",
    pattern: "x",
    min_length: "3",
    max_length: "",
    min: "0",
    max: " 100 ",
    choices: "a\nb",
    labels: "Total, Amount due",
    extract_pattern: "",
  };

  it("keeps only the rules that fit the type", () => {
    expect(toDefinition(draft)).toEqual({
      key: "total",
      label: "Total",
      data_type: "number",
      required: true,
      read_only: false,
      default_value: null,
      validation: { min: "0", max: "100" },
      extraction: { labels: ["Total", "Amount due"] },
      choices: [],
    });
  });

  it("turns text rules into numbers and choices into a list", () => {
    const text = toDefinition({ ...draft, data_type: "text", pattern: "INV-\\d+", max_length: "20" });
    expect(text.validation).toEqual({ pattern: "INV-\\d+", min_length: 3, max_length: 20 });
    const choice = toDefinition({ ...draft, data_type: "choice" });
    expect(choice.choices).toEqual([
      { value: "a", label: "a" },
      { value: "b", label: "b" },
    ]);
    expect(choice.validation).toEqual({});
  });
});

describe("parseLabels", () => {
  it("splits on commas and lines and drops blanks and repeats", () => {
    expect(parseLabels("Invoice no, Invoice number,\n Invoice no ,, Invoice no")).toEqual([
      "Invoice no",
      "Invoice number",
    ]);
  });
});
