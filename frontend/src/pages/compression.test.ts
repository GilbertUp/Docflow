import { describe, expect, it } from "vitest";

import { reductionText } from "./files/CompressionDialog";

describe("reductionText", () => {
  it("says how much smaller a copy is", () => {
    expect(reductionText({ source_size: 1000000, derivative_size: 250000, reduction: 0.75 })).toMatch(
      /75% smaller/,
    );
  });

  it("says plainly when no copy was kept", () => {
    expect(reductionText({ source_size: 1000, derivative_size: 0, reduction: null })).toBe(
      "No copy was kept.",
    );
  });
});
