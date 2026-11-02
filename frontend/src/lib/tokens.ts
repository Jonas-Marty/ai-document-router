/** "850", "3.4K", "128K": token counts short enough for a line that wraps on a phone, or a
 * stat tile. Exact figures stay available wherever this is used, in a details view. */
export function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 100_000) return `${(count / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  if (count < 1_000_000) return `${Math.round(count / 1000)}K`;
  return `${(count / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}
