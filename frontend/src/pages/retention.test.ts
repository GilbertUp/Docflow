import { describe, expect, it } from "vitest";

import { describeDays, toDays } from "./admin/Retention";
import { retentionSummary } from "./files/RetentionDialog";

describe("describeDays", () => {
  it("speaks in years when it can", () => {
    expect(describeDays(365)).toBe("1 year");
    expect(describeDays(2555)).toBe("7 years");
    expect(describeDays(30)).toBe("30 days");
    expect(describeDays(1)).toBe("1 day");
  });
});

describe("toDays", () => {
  it("turns years and days into days", () => {
    expect(toDays("7", "years")).toBe(2555);
    expect(toDays("90", "days")).toBe(90);
  });
});

describe("retentionSummary", () => {
  it("says what each state means", () => {
    expect(retentionSummary("held", null)).toMatch(/legal hold/);
    expect(retentionSummary("not_assigned", null)).toMatch(/No retention class/);
    expect(retentionSummary("eligible", null)).toMatch(/Retention has ended/);
    expect(retentionSummary("retained", "2030-01-01T00:00:00Z")).toMatch(/Until/);
  });
});
