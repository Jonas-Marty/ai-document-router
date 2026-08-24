const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["week", 60 * 60 * 24 * 7],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
];

const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/** "3 hours ago" style relative time, used for scan time (SPEC 8.3) and history rows (SPEC
 * 8.6, which also wants the absolute time on hover -- callers render that separately via the
 * `title` attribute on the same element, this only produces the relative label). */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const diffSeconds = (then.getTime() - now.getTime()) / 1000;

  if (Math.abs(diffSeconds) < 60) return "just now";

  for (const [unit, secondsInUnit] of UNITS) {
    if (Math.abs(diffSeconds) >= secondsInUnit) {
      return formatter.format(Math.round(diffSeconds / secondsInUnit), unit);
    }
  }
  return formatter.format(Math.round(diffSeconds / 60), "minute");
}
