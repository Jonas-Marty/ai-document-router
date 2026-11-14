/** "850", "3.4K", "128K": token counts short enough for a line that wraps on a phone, or a
 * stat tile. Exact figures stay available wherever this is used, in a details view. */
export function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 100_000) return `${(count / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  if (count < 1_000_000) return `${Math.round(count / 1000)}K`;
  return `${(count / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

/** The currency of a cost that spans two currencies and so could not be added up. */
export const MIXED_CURRENCY = "mixed";

/** A cost in the connection's own currency, "CHF 0.0123": four decimals below one, two above,
 * so a month of a cheap model does not read as 0.00. A cost recorded before currencies were
 * kept has none and shows as the bare number. */
export function formatCost(value: number, currency: string | null): string {
  const amount = value.toLocaleString(undefined, {
    minimumFractionDigits: value < 1 ? 4 : 2,
    maximumFractionDigits: value < 1 ? 4 : 2,
  });
  return currency && currency !== MIXED_CURRENCY ? `${currency} ${amount}` : amount;
}

/** Costs in several currencies side by side, since they cannot be added: "CHF 1.20 + USD 0.40". */
export function formatCosts(costs: { amount: number; currency: string | null }[]): string {
  return costs.map((cost) => formatCost(cost.amount, cost.currency)).join(" + ");
}
