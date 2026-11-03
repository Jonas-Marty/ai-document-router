import { describe, expect, it } from "vitest";
import type { AiUsageRow } from "@/services/api/types";
import { buckets, bucketUnit, groupRows, niceAxis, periodFor, totals } from "./aiUsage";

function row(overrides: Partial<AiUsageRow> = {}): AiUsageRow {
  return {
    day: "2026-11-02",
    endpoint_id: "ep",
    connection: "Tower",
    connection_exists: true,
    model: "qwen3",
    model_configured: true,
    feature: "filing",
    requests: 1,
    prompt_tokens: 100,
    completion_tokens: 10,
    cached_tokens: 0,
    without_usage: 0,
    failed: 0,
    fallback: 0,
    duration_ms: 1000,
    timed: 1,
    ...overrides,
  };
}

describe("totals", () => {
  it("averages tokens only over calls that reported them", () => {
    const sum = totals([
      row({ requests: 3, without_usage: 1, failed: 1, prompt_tokens: 90, completion_tokens: 10 }),
    ]);

    expect(sum.total).toBe(100);
    expect(sum.avgTokens).toBe(100);
  });

  it("has no averages for usage carried over without durations", () => {
    const sum = totals([row({ requests: 2, duration_ms: 0, timed: 0 })]);

    expect(sum.avgMs).toBeNull();
  });
});

describe("groupRows", () => {
  it("keys connections by id, so a renamed one stays one group", () => {
    const groups = groupRows(
      [row({ connection: "Tower" }), row({ connection: "Tower PC" })],
      "connection",
    );

    expect(groups).toHaveLength(1);
    expect(groups[0]?.totals.requests).toBe(2);
  });

  it("puts the largest first", () => {
    const groups = groupRows(
      [row({ model: "small", prompt_tokens: 1 }), row({ model: "big", prompt_tokens: 999 })],
      "model",
    );

    expect(groups.map((g) => g.key)).toEqual(["big", "small"]);
  });
});

describe("periods and buckets", () => {
  it("covers last month across a year boundary", () => {
    expect(periodFor("last", new Date(2026, 0, 15))).toEqual({
      kind: "last",
      from: "2025-12-01",
      to: "2025-12-31",
    });
  });

  it("buckets a month by day and a year by month, empty ones included", () => {
    const month = periodFor("month", new Date(2026, 10, 3));
    const year = periodFor("year", new Date(2026, 10, 3));

    expect(bucketUnit(month)).toBe("day");
    const days = buckets([row()], month, "model");
    expect(days).toHaveLength(30);
    expect(days[1]?.values.get("qwen3")).toBe(110);

    expect(bucketUnit(year)).toBe("month");
    expect(buckets([row()], year, "model").map((b) => b.key)[10]).toBe("2026-11");
  });

  it("rounds the axis to a 1-2-5 step", () => {
    expect(niceAxis(0)).toEqual({ top: 1, step: 1 });
    expect(niceAxis(3_700)).toEqual({ top: 4_000, step: 1_000 });
    expect(niceAxis(7)).toEqual({ top: 8, step: 2 });
  });
});
