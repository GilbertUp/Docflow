import { describe, expect, it } from "vitest";

import { hoursOrNull } from "./admin/Workflows";
import { withChange } from "./Notifications";

describe("withChange", () => {
  it("switches one kind and keeps the rest", () => {
    expect(
      withChange(
        [
          { kind: "mention", email: false },
          { kind: "task_assigned", email: true },
        ],
        "mention",
        true,
      ),
    ).toEqual({ mention: true, task_assigned: true });
  });
});

describe("hoursOrNull", () => {
  it("reads blank as none and anything else as a number", () => {
    expect(hoursOrNull("  ")).toBeNull();
    expect(hoursOrNull("24")).toBe(24);
    expect(hoursOrNull("0")).toBe(0);
  });
});
