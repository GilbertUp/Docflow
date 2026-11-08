import { describe, expect, it } from "vitest";

import { dueFromDate } from "./files/DiscussionDialog";
import { isOverdue } from "./Tasks";

describe("isOverdue", () => {
  const now = new Date("2026-11-10T12:00:00Z");
  it("is true only for open tasks past their due time", () => {
    expect(isOverdue({ status: "open", due_at: "2026-11-09T00:00:00Z" }, now)).toBe(true);
    expect(isOverdue({ status: "open", due_at: "2026-11-11T00:00:00Z" }, now)).toBe(false);
    expect(isOverdue({ status: "done", due_at: "2026-11-09T00:00:00Z" }, now)).toBe(false);
    expect(isOverdue({ status: "open", due_at: null }, now)).toBe(false);
  });
});

describe("dueFromDate", () => {
  it("turns a date into the end of that day, or nothing", () => {
    expect(dueFromDate("2026-12-01")).toBe("2026-12-01T23:59:00Z");
    expect(dueFromDate("")).toBeNull();
  });
});
