import { describe, expect, it } from "vitest";

import { barHeight, waitText } from "./admin/Operations";

describe("barHeight", () => {
  it("scales to the busiest day and always shows some activity", () => {
    expect(barHeight(10, 10)).toBe(100);
    expect(barHeight(5, 10)).toBe(50);
    expect(barHeight(1, 1000)).toBe(4);
    expect(barHeight(0, 10)).toBe(0);
    expect(barHeight(3, 0)).toBe(0);
  });
});

describe("waitText", () => {
  it("uses the largest sensible unit", () => {
    expect(waitText(null)).toBe("nothing is waiting");
    expect(waitText(45)).toBe("45 seconds");
    expect(waitText(600)).toBe("10 minutes");
    expect(waitText(7200)).toBe("2 hours");
  });
});
