import { describe, expect, it } from "vitest";

import { toggleEvent } from "./admin/Webhooks";

describe("toggleEvent", () => {
  const all = ["a", "b", "c"];
  it("adds an event and keeps the order the server lists them in", () => {
    expect(toggleEvent(all, ["c"], "a", true)).toEqual(["a", "c"]);
  });
  it("removes an event", () => {
    expect(toggleEvent(all, ["a", "b"], "a", false)).toEqual(["b"]);
  });
  it("does not add the same event twice", () => {
    expect(toggleEvent(all, ["a"], "a", true)).toEqual(["a"]);
  });
});
