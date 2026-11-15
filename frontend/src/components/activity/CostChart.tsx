import { bucketLabel, COST_KINDS, type CostBucket } from "@/lib/aiCosts";
import { formatCost } from "@/lib/tokens";
import { cn } from "@/lib/utils";
import type { CostKind } from "@/services/api/types";

/** One colour per kind, told apart by lightness as well as hue; failures stand out. */
export const KIND_COLORS: Record<CostKind, string> = {
  first: "bg-blue-700 dark:bg-blue-500",
  reanalysis: "bg-blue-300 dark:bg-blue-800",
  failed: "bg-orange-600 dark:bg-orange-500",
  other: "bg-neutral-400 dark:bg-neutral-600",
};

/** SPEC 8.11: cost per day (per month for a long period), stacked by kind. The bars are
 * decoration for the totals beside the chart, which carry the same figures as text, so each
 * bar only names its own total on hover. */
export function CostChart({
  buckets,
  currency,
}: {
  buckets: CostBucket[];
  currency: string | null;
}) {
  const top = Math.max(...buckets.map((bucket) => bucket.total), 0);
  const first = buckets[0];
  const middle = buckets[Math.floor(buckets.length / 2)];
  const last = buckets[buckets.length - 1];

  return (
    <figure className="space-y-2">
      <figcaption className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
        <span>Cost per {first && first.key.length === 7 ? "month" : "day"}</span>
        <span className="flex flex-wrap gap-x-3.5 gap-y-1">
          {COST_KINDS.map(({ kind, label }) => (
            <span key={kind} className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className={cn("size-2.5 rounded-sm", KIND_COLORS[kind])} />
              {label}
            </span>
          ))}
        </span>
      </figcaption>
      <div aria-hidden="true" className="flex h-40 items-end gap-px border-b sm:gap-1">
        {buckets.map((bucket) => (
          <div
            key={bucket.key}
            className="flex h-full min-w-0 flex-1 flex-col-reverse"
            title={`${bucketLabel(bucket.key)} · ${formatCost(bucket.total, currency)}`}
          >
            {COST_KINDS.map(({ kind }) =>
              bucket.values[kind] > 0 && top > 0 ? (
                <div
                  key={kind}
                  className={cn("w-full last:rounded-t-sm", KIND_COLORS[kind])}
                  style={{ height: `${(bucket.values[kind] / top) * 100}%` }}
                />
              ) : null,
            )}
          </div>
        ))}
      </div>
      {first && last && (
        <div aria-hidden="true" className="flex justify-between text-[11px] text-muted-foreground">
          <span>{bucketLabel(first.key)}</span>
          {middle && buckets.length > 2 && <span>{bucketLabel(middle.key)}</span>}
          <span>{bucketLabel(last.key)}</span>
        </div>
      )}
    </figure>
  );
}
