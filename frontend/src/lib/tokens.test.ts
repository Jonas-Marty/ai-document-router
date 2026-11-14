import { describe, expect, it } from "vitest";
import { formatCost, formatCosts, formatTokens, MIXED_CURRENCY } from "./tokens";

describe("formatTokens", () => {
  it.each([
    [0, "0"],
    [850, "850"],
    [1000, "1K"],
    [3412, "3.4K"],
    [128_400, "128K"],
    [2_450_000, "2.5M"],
  ])("formats %i as %s", (count, expected) => {
    expect(formatTokens(count)).toBe(expected);
  });
});

describe("formatCost", () => {
  it("names the currency, with four places below one", () => {
    expect(formatCost(0.0123, "CHF")).toBe(
      `CHF ${(0.0123).toLocaleString(undefined, { minimumFractionDigits: 4 })}`,
    );
    expect(formatCost(12, "USD")).toBe(
      `USD ${(12).toLocaleString(undefined, { minimumFractionDigits: 2 })}`,
    );
  });

  it("shows a cost from before currencies, or a mixed one, as the bare number", () => {
    expect(formatCost(2, null)).toBe((2).toLocaleString(undefined, { minimumFractionDigits: 2 }));
    expect(formatCost(2, MIXED_CURRENCY)).not.toContain(MIXED_CURRENCY);
  });

  it("puts costs in different currencies side by side", () => {
    expect(
      formatCosts([
        { amount: 1, currency: "CHF" },
        { amount: 2, currency: "USD" },
      ]),
    ).toMatch(/^CHF .* \+ USD /);
  });
});
