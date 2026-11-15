import { describe, expect, it } from "vitest";
import { comparisonFor, costBuckets, costPeriod, forecast } from "./aiCosts";

const NOV_15 = new Date(2026, 10, 15, 12);
const MAR_31 = new Date(2026, 2, 31, 12);

describe("costPeriod", () => {
  it("runs this month to its end but charts it only up to today", () => {
    expect(costPeriod("month", NOV_15)).toEqual({
      kind: "month",
      from: "2026-11-01",
      to: "2026-11-30",
      through: "2026-11-15",
    });
  });

  it("takes last month whole, across a year end", () => {
    expect(costPeriod("last", new Date(2026, 0, 10))).toMatchObject({
      from: "2025-12-01",
      to: "2025-12-31",
    });
  });

  it("counts the last 30 days including today", () => {
    expect(costPeriod("last30", NOV_15)).toMatchObject({ from: "2026-10-17", to: "2026-11-15" });
  });
});

describe("comparisonFor", () => {
  it("sets this month against the same days of the last, cut to that month's length", () => {
    expect(comparisonFor(costPeriod("month", MAR_31), MAR_31)).toEqual({
      from: "2026-02-01",
      to: "2026-02-28",
      label: "the same days last month",
    });
  });

  it("has nothing to set a year against", () => {
    expect(comparisonFor(costPeriod("year", NOV_15), NOV_15)).toBeNull();
  });

  it("sets the last 30 days against the 30 before them", () => {
    expect(comparisonFor(costPeriod("last30", NOV_15), NOV_15)).toMatchObject({
      from: "2026-09-17",
      to: "2026-10-16",
    });
  });
});

describe("forecast", () => {
  it("extends this month's pace to its end, and only this month's", () => {
    expect(forecast(costPeriod("month", NOV_15), 3, NOV_15)).toBeCloseTo(6);
    expect(forecast(costPeriod("last", NOV_15), 3, NOV_15)).toBeNull();
    expect(forecast(costPeriod("month", NOV_15), 0, NOV_15)).toBeNull();
  });
});

describe("costBuckets", () => {
  const day = (d: string, first: number) => ({
    day: d,
    first,
    reanalysis: 0,
    failed: 0.5,
    other: 0,
  });

  it("has every day up to today, empty ones included", () => {
    const buckets = costBuckets([day("2026-11-03", 1)], costPeriod("month", NOV_15));

    expect(buckets).toHaveLength(15);
    expect(buckets[2]).toMatchObject({ key: "2026-11-03", total: 1.5 });
    expect(buckets[0]?.total).toBe(0);
  });

  it("goes by month for a year", () => {
    const buckets = costBuckets(
      [day("2026-03-03", 1), day("2026-03-20", 2)],
      costPeriod("year", NOV_15),
    );

    expect(buckets.map((bucket) => bucket.key)).toHaveLength(11);
    expect(buckets[2]).toMatchObject({ key: "2026-03", total: 4 });
  });
});
