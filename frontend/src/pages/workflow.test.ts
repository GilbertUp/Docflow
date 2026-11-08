import { describe, expect, it } from "vitest";

import { toSteps, toTrigger } from "./admin/Workflows";
import { eventText } from "./files/WorkflowDialog";
import { endOfDay, startOfDay } from "./Tasks";

describe("toSteps", () => {
  it("trims names and turns blank days into none", () => {
    expect(
      toSteps([
        { id: 1, name: " Check ", kind: "approval", assignee: "u1", days: "" },
        { id: 2, name: "File", kind: "task", assignee: "u2", days: "3" },
      ]),
    ).toEqual([
      { name: "Check", kind: "approval", assignee_id: "u1", due_days: null },
      { name: "File", kind: "task", assignee_id: "u2", due_days: 3 },
    ]);
  });
});

describe("toTrigger", () => {
  it("starts on a type, or by hand when none is chosen", () => {
    expect(toTrigger("t1")).toEqual({ event: "document.typed", document_type_id: "t1" });
    expect(toTrigger("")).toEqual({});
  });
});

describe("eventText", () => {
  it("words each kind of history entry", () => {
    expect(eventText("started", { automatic: true })).toBe("Started by itself");
    expect(eventText("started", {})).toBe("Started");
    expect(eventText("decided", { decision: "reject" })).toBe("Decided: reject");
    expect(eventText("cancelled", {})).toBe("Stopped");
  });
});

describe("delegation dates", () => {
  it("covers the whole of the days chosen", () => {
    expect(new Date(startOfDay("2026-12-01")) < new Date(endOfDay("2026-12-01"))).toBe(true);
  });
});
