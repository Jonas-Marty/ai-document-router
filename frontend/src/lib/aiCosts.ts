/** The arithmetic behind the cost section on Activity (SPEC 8.11): its periods, the period
 * it is compared with, the month's forecast and the chart's buckets. */
import type { CostKind, DayCost } from "@/services/api/types";
import { nextDay } from "./aiUsage";

export type CostPeriodKind = "month" | "last" | "last30" | "year";

export const COST_PERIODS: { kind: CostPeriodKind; label: string }[] = [
  { kind: "month", label: "This month" },
  { kind: "last", label: "Last month" },
  { kind: "last30", label: "Last 30 days" },
  { kind: "year", label: "This year" },
];

export const COST_KINDS: { kind: CostKind; label: string }[] = [
  { kind: "first", label: "First analyses" },
  { kind: "reanalysis", label: "Re-analyses" },
  { kind: "failed", label: "Failed" },
  { kind: "other", label: "Lab and other" },
];

export interface CostPeriod {
  kind: CostPeriodKind;
  /** First day, inclusive, YYYY-MM-DD. */
  from: string;
  /** Last day, inclusive. */
  to: string;
  /** The last day that has happened yet: the chart stops there. */
  through: string;
}

export interface Comparison {
  from: string;
  to: string;
  label: string;
}

const pad = (n: number) => String(n).padStart(2, "0");
const iso = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`;
const daysIn = (y: number, m: number) => new Date(y, m, 0).getDate();
const localDay = (date: Date) => iso(date.getFullYear(), date.getMonth() + 1, date.getDate());

function shift(day: string, days: number): string {
  const [y = 0, m = 1, d = 1] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

function previousMonth(y: number, m: number): [number, number] {
  return m === 1 ? [y - 1, 12] : [y, m - 1];
}

export function costPeriod(kind: CostPeriodKind, today = new Date()): CostPeriod {
  const y = today.getFullYear();
  const m = today.getMonth() + 1;
  const now = localDay(today);
  if (kind === "month")
    return { kind, from: iso(y, m, 1), to: iso(y, m, daysIn(y, m)), through: now };
  if (kind === "last") {
    const [ly, lm] = previousMonth(y, m);
    const to = iso(ly, lm, daysIn(ly, lm));
    return { kind, from: iso(ly, lm, 1), to, through: to };
  }
  if (kind === "last30") return { kind, from: shift(now, -29), to: now, through: now };
  return { kind, from: iso(y, 1, 1), to: iso(y, 12, 31), through: now };
}

/** What "spent" is set against: the same days of last month, the month before, or the 30
 * days before. None for a year, which has nothing like-for-like this early in it. */
export function comparisonFor(period: CostPeriod, today = new Date()): Comparison | null {
  const [y = 0, m = 1] = period.from.split("-").map(Number);
  if (period.kind === "month") {
    const [py, pm] = previousMonth(y, m);
    const day = Math.min(today.getDate(), daysIn(py, pm));
    return { from: iso(py, pm, 1), to: iso(py, pm, day), label: "the same days last month" };
  }
  if (period.kind === "last") {
    const [py, pm] = previousMonth(y, m);
    return { from: iso(py, pm, 1), to: iso(py, pm, daysIn(py, pm)), label: "the month before" };
  }
  if (period.kind === "last30") {
    return {
      from: shift(period.from, -30),
      to: shift(period.from, -1),
      label: "the 30 days before",
    };
  }
  return null;
}

/** The month's spend at the pace so far; only this month has a rest to forecast. */
export function forecast(period: CostPeriod, spent: number, today = new Date()): number | null {
  if (period.kind !== "month" || spent <= 0) return null;
  const elapsed = today.getDate();
  return (spent / elapsed) * daysIn(today.getFullYear(), today.getMonth() + 1);
}

export interface CostBucket {
  key: string;
  values: Record<CostKind, number>;
  total: number;
}

const empty = (): Record<CostKind, number> => ({ first: 0, reanalysis: 0, failed: 0, other: 0 });

/** Every day up to today, or every month for a period longer than about two months, empty
 * ones included so the axis is even. */
export function costBuckets(days: DayCost[], period: CostPeriod): CostBucket[] {
  const span = (Date.parse(nextDay(period.through)) - Date.parse(period.from)) / 86_400_000;
  const monthly = span > 62;
  const keys: string[] = [];
  for (let day = period.from; day <= period.through; day = nextDay(day)) {
    const key = monthly ? day.slice(0, 7) : day;
    if (keys[keys.length - 1] !== key) keys.push(key);
  }
  const buckets = new Map(keys.map((key) => [key, empty()]));
  for (const day of days) {
    const values = buckets.get(monthly ? day.day.slice(0, 7) : day.day);
    if (!values) continue;
    for (const { kind } of COST_KINDS) values[kind] += day[kind];
  }
  return keys.map((key) => {
    const values = buckets.get(key) ?? empty();
    return { key, values, total: COST_KINDS.reduce((sum, { kind }) => sum + values[kind], 0) };
  });
}

export function bucketLabel(key: string): string {
  const [y = 0, m = 1, d] = key.split("-").map(Number);
  const date = new Date(y, m - 1, d ?? 1);
  return d === undefined
    ? date.toLocaleDateString("en", { month: "short", year: "numeric" })
    : date.toLocaleDateString("en", { day: "numeric", month: "short" });
}
