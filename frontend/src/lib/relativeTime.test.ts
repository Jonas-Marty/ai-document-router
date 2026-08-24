import { describe, expect, it } from "vitest";
import { relativeTime } from "./relativeTime";

describe("relativeTime", () => {
  const now = new Date("2026-05-18T12:00:00Z");

  it("says 'just now' for anything under a minute", () => {
    expect(relativeTime("2026-05-18T11:59:30Z", now)).toBe("just now");
  });

  it("formats a past hour", () => {
    expect(relativeTime("2026-05-18T09:00:00Z", now)).toBe("3 hours ago");
  });

  it("formats a past day", () => {
    expect(relativeTime("2026-05-16T12:00:00Z", now)).toBe("2 days ago");
  });

  it("formats a future time (e.g. clock skew) without crashing", () => {
    expect(relativeTime("2026-05-18T13:00:00Z", now)).toBe("in 1 hour");
  });
});
