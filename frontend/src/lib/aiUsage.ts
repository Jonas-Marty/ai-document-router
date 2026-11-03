/** The figures behind the AI usage page, from GET /ai/usage rows (one per day, connection,
 * model and feature): totals, a grouping, chart buckets and the period shown. */
import type { AiUsageRow } from "@/services/api/types";

export interface UsageTotals {
  requests: number;
  prompt: number;
  completion: number;
  total: number;
  cached: number;
  withoutUsage: number;
  failed: number;
  fallback: number;
  /** Mean duration over the calls that recorded one; null when none did. */
  avgMs: number | null;
  /** Mean tokens per call that reported tokens; null when none did. */
  avgTokens: number | null;
}

export function totals(rows: AiUsageRow[]): UsageTotals {
  let requests = 0;
  let prompt = 0;
  let completion = 0;
  let cached = 0;
  let withoutUsage = 0;
  let failed = 0;
  let fallback = 0;
  let duration = 0;
  let timed = 0;
  for (const row of rows) {
    requests += row.requests;
    prompt += row.prompt_tokens;
    completion += row.completion_tokens;
    cached += row.cached_tokens;
    withoutUsage += row.without_usage;
    failed += row.failed;
    fallback += row.fallback;
    duration += row.duration_ms;
    timed += row.timed;
  }
  const total = prompt + completion;
  // A failed call has no reply to count tokens from, so it is not part of the average either.
  const counted = requests - withoutUsage - failed;
  return {
    requests,
    prompt,
    completion,
    total,
    cached,
    withoutUsage,
    failed,
    fallback,
    avgMs: timed > 0 ? duration / timed : null,
    avgTokens: counted > 0 ? total / counted : null,
  };
}

export type GroupBy = "connection" | "model" | "feature";

/** A connection is keyed by id; usage that never matched one, by name. */
export function connectionKey(row: AiUsageRow): string {
  return row.endpoint_id ?? `name:${row.connection}`;
}

export function groupKey(row: AiUsageRow, by: GroupBy): string {
  if (by === "connection") return connectionKey(row);
  if (by === "model") return row.model;
  return row.feature;
}

export interface UsageGroup {
  key: string;
  rows: AiUsageRow[];
  totals: UsageTotals;
}

/** Groups, largest total first; ties fall back to requests, then name, so the order is stable. */
export function groupRows(rows: AiUsageRow[], by: GroupBy): UsageGroup[] {
  const map = new Map<string, AiUsageRow[]>();
  for (const row of rows) {
    const key = groupKey(row, by);
    const list = map.get(key);
    if (list) list.push(row);
    else map.set(key, [row]);
  }
  return [...map.entries()]
    .map(([key, list]) => ({ key, rows: list, totals: totals(list) }))
    .sort(
      (a, b) =>
        b.totals.total - a.totals.total ||
        b.totals.requests - a.totals.requests ||
        a.key.localeCompare(b.key),
    );
}

export type PeriodKind = "month" | "last" | "year" | "custom";

export interface Period {
  kind: PeriodKind;
  /** First day, inclusive, YYYY-MM-DD. */
  from: string;
  /** Last day, inclusive, YYYY-MM-DD. */
  to: string;
}

const pad = (n: number) => String(n).padStart(2, "0");
const iso = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`;
const lastDay = (y: number, m: number) => new Date(y, m, 0).getDate();

export function periodFor(kind: Exclude<PeriodKind, "custom">, today = new Date()): Period {
  const y = today.getFullYear();
  const m = today.getMonth() + 1;
  if (kind === "month") return { kind, from: iso(y, m, 1), to: iso(y, m, lastDay(y, m)) };
  if (kind === "last") {
    const ly = m === 1 ? y - 1 : y;
    const lm = m === 1 ? 12 : m - 1;
    return { kind, from: iso(ly, lm, 1), to: iso(ly, lm, lastDay(ly, lm)) };
  }
  return { kind, from: iso(y, 1, 1), to: iso(y, 12, 31) };
}

export function nextDay(day: string): string {
  const [y = 0, m = 1, d = 1] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
}

/** Days for a range of up to about two months, months beyond that. */
export function bucketUnit(period: Period): "day" | "month" {
  const days = (Date.parse(nextDay(period.to)) - Date.parse(period.from)) / 86_400_000;
  return days <= 62 ? "day" : "month";
}

export interface Bucket {
  key: string;
  /** Total tokens per group key. */
  values: Map<string, number>;
}

/** Every day or month of the period, empty ones included, so the axis is even. */
export function buckets(rows: AiUsageRow[], period: Period, by: GroupBy): Bucket[] {
  const unit = bucketUnit(period);
  const keys: string[] = [];
  if (unit === "day") {
    for (let d = period.from; d <= period.to; d = nextDay(d)) keys.push(d);
  } else {
    const [startYear = 0, startMonth = 1] = period.from.split("-").map(Number);
    let y = startYear;
    let m = startMonth;
    const end = period.to.slice(0, 7);
    for (;;) {
      const key = `${y}-${pad(m)}`;
      keys.push(key);
      if (key >= end) break;
      m += 1;
      if (m > 12) {
        m = 1;
        y += 1;
      }
    }
  }
  const out = new Map(keys.map((key) => [key, new Map<string, number>()]));
  for (const row of rows) {
    const bucket = out.get(unit === "day" ? row.day : row.day.slice(0, 7));
    if (!bucket) continue;
    const group = groupKey(row, by);
    bucket.set(group, (bucket.get(group) ?? 0) + row.prompt_tokens + row.completion_tokens);
  }
  return keys.map((key) => ({ key, values: out.get(key) ?? new Map() }));
}

/** The top of the y axis and its step: 1-2-5 steps, about four grid lines, so the axis
 * follows the largest bar on screen. */
export function niceAxis(max: number): { top: number; step: number } {
  if (!(max > 0)) return { top: 1, step: 1 };
  const raw = max / 4;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  // Tokens are whole numbers: never a fractional step.
  const step = Math.max(
    1,
    [1, 2, 5, 10].map((f) => f * magnitude).find((x) => x >= raw) ?? 10 * magnitude,
  );
  return { top: Math.ceil(max / step) * step, step };
}

export const FEATURE_LABELS: Record<string, string> = {
  extraction: "Reading",
  filing: "Filing",
  comparison: "Comparisons",
};
