import { describe, expect, it } from "vitest";
import { formatTokens } from "./tokens";

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
