import { describe, expect, it } from "vitest";

import { NO_FILTERS, toQuery, verificationText } from "./admin/AuditLog";

describe("toQuery", () => {
  it("leaves out blank filters", () => {
    expect(toQuery(NO_FILTERS)).toEqual({});
    expect(toQuery({ ...NO_FILTERS, actionPrefix: " auth. ", result: "failure" })).toEqual({
      action_prefix: "auth.",
      result: "failure",
    });
  });

  it("makes the last day include the whole day", () => {
    const query = toQuery({ ...NO_FILTERS, since: "2026-11-01", until: "2026-11-01" });
    expect(new Date(query.until ?? "").getTime() - new Date(query.since ?? "").getTime()).toBe(
      24 * 60 * 60 * 1000,
    );
  });
});

describe("verificationText", () => {
  const base = { head_hash: null, checkpoint_reason: null };
  it("says when the trail is intact", () => {
    const text = verificationText({
      ...base, ok: true, events: 12, first_bad_seq: null, reason: null, checkpoints_ok: true, checkpoints: 2,
    });
    expect(text).toMatch(/intact: 12 events/);
  });
  it("names the first bad event", () => {
    const text = verificationText({
      ...base, ok: false, events: 4, first_bad_seq: 5, reason: "content changed", checkpoints_ok: true, checkpoints: 0,
    });
    expect(text).toMatch(/number 5/);
  });
  it("says when only an outside checkpoint disagrees", () => {
    const text = verificationText({
      ...base, ok: false, events: 9, first_bad_seq: null, reason: null, checkpoints_ok: false, checkpoints: 0,
    });
    expect(text).toMatch(/outside the database/);
  });
});
