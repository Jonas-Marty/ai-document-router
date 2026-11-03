import { ArrowLeft, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useAiUsage } from "@/hooks/useAiUsage";
import { useIsDesktop } from "@/hooks/useBreakpoint";
import {
  buckets,
  bucketUnit,
  FEATURE_LABELS,
  type GroupBy,
  groupRows,
  niceAxis,
  type Period,
  type PeriodKind,
  periodFor,
  totals,
  type UsageGroup,
  type UsageTotals,
} from "@/lib/aiUsage";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api/errors";
import type { AiUsageRow } from "@/services/api/types";

const SERIES = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

const fmt = (n: number) => Math.round(n).toLocaleString("en");
const seconds = (ms: number | null) => (ms === null ? "–" : `${(ms / 1000).toFixed(1)} s`);

function fmtDay(day: string, options: Intl.DateTimeFormatOptions): string {
  return new Date(`${day}T00:00:00`).toLocaleDateString("en", options);
}

/** How many tokens each connection, model and feature used over a period: totals, a chart
 * and a breakdown. Reached from Activity; the account's own usage only. */
export default function UsagePage() {
  const [periodKind, setPeriodKind] = useState<PeriodKind>("month");
  const [custom, setCustom] = useState(() => ({
    from: periodFor("year").from,
    to: periodFor("month").to,
  }));
  const [groupBy, setGroupBy] = useState<GroupBy>("connection");
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [open, setOpen] = useState<Set<string>>(() => new Set());

  const period: Period =
    periodKind === "custom"
      ? {
          kind: "custom",
          from: custom.from <= custom.to ? custom.from : custom.to,
          to: custom.from <= custom.to ? custom.to : custom.from,
        }
      : periodFor(periodKind);
  const usage = useAiUsage(period.from, period.to);
  const rows = usage.data?.rows ?? [];
  const sum = totals(rows);
  const groups = groupRows(rows, groupBy);

  const labelOf = (group: UsageGroup) =>
    groupBy === "connection"
      ? (group.rows[0]?.connection ?? group.key)
      : groupBy === "model"
        ? group.key
        : (FEATURE_LABELS[group.key] ?? group.key);
  const colorOf = (key: string) =>
    SERIES[
      Math.max(
        0,
        groups.findIndex((g) => g.key === key),
      ) % SERIES.length
    ] ?? "var(--chart-1)";
  const rangeLabel =
    periodKind === "month" || periodKind === "last"
      ? fmtDay(period.from, { month: "long", year: "numeric" })
      : `${fmtDay(period.from, { day: "numeric", month: "short", year: "numeric" })} – ${fmtDay(
          period.to,
          { day: "numeric", month: "short", year: "numeric" },
        )}`;

  function toggle(set: Set<string>, key: string): Set<string> {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <Link
            to="/activity"
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" aria-hidden="true" /> Activity
          </Link>
          <h1 className="text-lg font-semibold">AI usage</h1>
        </div>
        <Segmented
          label="Period"
          value={periodKind}
          onChange={(value) => setPeriodKind(value as PeriodKind)}
          options={[
            ["month", "This month"],
            ["last", "Last month"],
            ["year", "This year"],
            ["custom", "Range…"],
          ]}
        />
      </div>

      {periodKind === "custom" && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Label htmlFor="usage-from" className="text-muted-foreground">
            From
          </Label>
          <Input
            id="usage-from"
            type="date"
            className="h-9 w-auto"
            value={custom.from}
            onChange={(e) => e.target.value && setCustom((c) => ({ ...c, from: e.target.value }))}
          />
          <Label htmlFor="usage-to" className="text-muted-foreground">
            to
          </Label>
          <Input
            id="usage-to"
            type="date"
            className="h-9 w-auto"
            value={custom.to}
            onChange={(e) => e.target.value && setCustom((c) => ({ ...c, to: e.target.value }))}
          />
        </div>
      )}
      <p className="-mt-2 text-sm text-muted-foreground">{rangeLabel}</p>

      {usage.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : usage.isError ? (
        <ErrorState
          message={usage.error instanceof ApiError ? usage.error.message : "Couldn't load usage."}
          onRetry={() => usage.refetch()}
        />
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3 lg:grid-cols-6">
            <Tile
              label="Requests"
              value={fmt(sum.requests)}
              sub={`${fmt(usage.data?.analyses ?? 0)} analyses · ${fmt(usage.data?.comparisons ?? 0)} comparisons`}
            />
            <Tile
              label="Tokens in"
              value={fmt(sum.prompt)}
              sub={sum.cached ? `${fmt(sum.cached)} of them cached` : undefined}
            />
            <Tile label="Tokens out" value={fmt(sum.completion)} />
            <Tile
              label="Total"
              value={fmt(sum.total)}
              sub={sum.avgTokens !== null ? `Ø ${fmt(sum.avgTokens)} / request` : undefined}
            />
            <Tile
              label="Failed"
              value={fmt(sum.failed)}
              sub={sum.failed ? "no reply; the next step was tried" : undefined}
              bad={sum.failed > 0}
            />
            <Tile
              label="Without counts"
              value={fmt(sum.withoutUsage)}
              sub={sum.withoutUsage ? "the server sent no token figures" : undefined}
            />
          </dl>

          {rows.length === 0 ? (
            <Card>
              <EmptyState
                title="No AI requests in this period."
                description="Analyses and comparisons show up here once a document has been read."
              />
            </Card>
          ) : (
            <>
              <Card className="gap-3 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Segmented
                    label="Group by"
                    value={groupBy}
                    onChange={(value) => {
                      setGroupBy(value as GroupBy);
                      setHidden(new Set());
                      setOpen(new Set());
                    }}
                    options={[
                      ["connection", "Connection"],
                      ["model", "Model"],
                      ["feature", "Feature"],
                    ]}
                  />
                  <span className="text-xs text-muted-foreground">
                    Tokens per {bucketUnit(period)}
                  </span>
                </div>
                <UsageChart
                  rows={rows}
                  period={period}
                  groupBy={groupBy}
                  order={groups.map((g) => g.key)}
                  hidden={hidden}
                  colorOf={colorOf}
                  labelOf={(key) => {
                    const group = groups.find((g) => g.key === key);
                    return group ? labelOf(group) : key;
                  }}
                />
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  {groups.map((group) => (
                    <button
                      key={group.key}
                      type="button"
                      aria-pressed={!hidden.has(group.key)}
                      onClick={() => setHidden((h) => toggle(h, group.key))}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded px-0.5 py-1 hover:text-foreground",
                        hidden.has(group.key) && "line-through opacity-40",
                      )}
                    >
                      <i
                        className="size-2.5 rounded-sm"
                        style={{ background: colorOf(group.key) }}
                      />
                      {labelOf(group)}
                    </button>
                  ))}
                </div>
              </Card>

              <Breakdown
                groups={groups}
                groupBy={groupBy}
                total={sum}
                open={open}
                onToggle={(key) => setOpen((o) => toggle(o, key))}
                labelOf={labelOf}
                colorOf={colorOf}
              />
            </>
          )}

          <div className="space-y-1 text-xs text-muted-foreground">
            <p>
              Days are UTC days. A request is one call to a model; an analysis that reads a scan
              page by page makes several.
            </p>
            <p>
              Usage from before calls were recorded one by one is carried over from the Activity
              log, counted per stage, without durations, failures or fallbacks.
            </p>
          </div>
        </>
      )}
    </div>
  );
}

function Segmented({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: [string, string][];
}) {
  return (
    <fieldset className="inline-flex flex-wrap overflow-hidden rounded-md border border-border bg-card">
      <legend className="sr-only">{label}</legend>
      {options.map(([option, text], i) => (
        <button
          key={option}
          type="button"
          aria-pressed={value === option}
          onClick={() => onChange(option)}
          className={cn(
            "px-3 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            i > 0 && "border-l border-border",
            value === option ? "bg-primary text-primary-foreground" : "hover:bg-muted",
          )}
        >
          {text}
        </button>
      ))}
    </fieldset>
  );
}

function Tile({
  label,
  value,
  sub,
  bad,
}: {
  label: string;
  value: string;
  sub?: string;
  bad?: boolean;
}) {
  return (
    <div className="bg-card px-4 py-3">
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className={cn("text-lg font-bold tabular-nums", bad && "text-destructive")}>{value}</dd>
      {sub && <dd className="text-[11px] text-muted-foreground">{sub}</dd>}
    </div>
  );
}

function UsageChart({
  rows,
  period,
  groupBy,
  order,
  hidden,
  colorOf,
  labelOf,
}: {
  rows: AiUsageRow[];
  period: Period;
  groupBy: GroupBy;
  order: string[];
  hidden: Set<string>;
  colorOf: (key: string) => string;
  labelOf: (key: string) => string;
}) {
  const isDesktop = useIsDesktop();
  const bars = buckets(rows, period, groupBy);
  const shown = order.filter((key) => !hidden.has(key));
  const max = Math.max(
    0,
    ...bars.map((b) => shown.reduce((s, key) => s + (b.values.get(key) ?? 0), 0)),
  );
  const { top, step } = niceAxis(max);
  // A narrower drawing on a phone keeps the labels readable once it is scaled down.
  const W = isDesktop ? 940 : 400;
  const H = isDesktop ? 220 : 200;
  const L = isDesktop ? 56 : 40;
  const R = 8;
  const T = 10;
  const B = 26;
  const ih = H - T - B;
  const y = (v: number) => T + ih - (v / top) * ih;
  const bw = (W - L - R) / bars.length;
  const unit = bucketUnit(period);
  const every = Math.ceil(bars.length / (isDesktop ? (unit === "day" ? 10 : 12) : 6));
  const tick = (v: number) =>
    v >= 1000 ? `${(Math.round(v / 100) / 10).toLocaleString("en")}k` : String(v);
  const label = (key: string) =>
    unit === "day" ? String(Number(key.slice(8))) : fmtDay(`${key}-01`, { month: "short" });
  const lines: number[] = [];
  for (let v = 0; v <= top + step / 2; v += step) lines.push(v);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-auto w-full"
      role="img"
      aria-label="Tokens over time"
    >
      {lines.map((v) => (
        <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} style={{ stroke: "var(--border)" }} />
          <text
            x={L - 6}
            y={y(v) + 4}
            textAnchor="end"
            fontSize={11}
            style={{ fill: "var(--muted-foreground)" }}
          >
            {tick(v)}
          </text>
        </g>
      ))}
      {bars.map((bar, i) => {
        let stacked = 0;
        const x = L + i * bw + bw * 0.15;
        const w = Math.max(1, bw * 0.7);
        return (
          <g key={bar.key}>
            {shown.map((key) => {
              const v = bar.values.get(key) ?? 0;
              if (!v) return null;
              const y1 = y(stacked + v);
              const y0 = y(stacked);
              stacked += v;
              return (
                <rect
                  key={key}
                  x={x}
                  y={y1}
                  width={w}
                  height={Math.max(0, y0 - y1)}
                  rx={1.5}
                  style={{ fill: colorOf(key) }}
                >
                  <title>{`${unit === "day" ? fmtDay(bar.key, { day: "numeric", month: "short" }) : label(bar.key)} · ${labelOf(key)}: ${fmt(v)} tokens`}</title>
                </rect>
              );
            })}
            {(i % every === 0 || i === bars.length - 1) && (
              <text
                x={L + i * bw + bw / 2}
                y={H - 8}
                textAnchor="middle"
                fontSize={11}
                style={{ fill: "var(--muted-foreground)" }}
              >
                {label(bar.key)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** Fallback use, a model no longer in any chain, a deleted connection. */
function Markers({
  rows,
  sum,
  level,
}: {
  rows: AiUsageRow[];
  sum: UsageTotals;
  level: "group" | "model";
}) {
  const deleted = rows.every((r) => !r.connection_exists);
  const retired = level === "model" && !deleted && rows.every((r) => !r.model_configured);
  const pill = "ml-1.5 whitespace-nowrap rounded-full px-1.5 text-[10.5px] leading-4";
  return (
    <>
      {sum.fallback > 0 && (
        <span
          className={cn(pill, "bg-amber-500/15 font-medium text-amber-700 dark:text-amber-300")}
        >
          {fmt(sum.fallback)} as fallback
        </span>
      )}
      {retired && (
        <span className={cn(pill, "border border-border text-muted-foreground")}>
          no longer in a chain
        </span>
      )}
      {deleted && level === "group" && (
        <span className={cn(pill, "border border-border text-muted-foreground")}>removed</span>
      )}
    </>
  );
}

function Breakdown({
  groups,
  groupBy,
  total,
  open,
  onToggle,
  labelOf,
  colorOf,
}: {
  groups: UsageGroup[];
  groupBy: GroupBy;
  total: UsageTotals;
  open: Set<string>;
  onToggle: (key: string) => void;
  labelOf: (group: UsageGroup) => string;
  colorOf: (key: string) => string;
}) {
  const isDesktop = useIsDesktop();
  // A connection opens into the models it ran; models and features are already one level.
  const nested = groupBy === "connection";
  const share = (n: number) => (total.total > 0 ? (n / total.total) * 100 : 0);
  const models = (group: UsageGroup) => groupRows(group.rows, "model");
  const markerLevel = groupBy === "model" ? "model" : "group";

  if (!isDesktop) {
    return (
      <div className="grid gap-2">
        {groups.map((group) => (
          <Fragment key={group.key}>
            <UsageCard
              title={labelOf(group)}
              markers={<Markers rows={group.rows} sum={group.totals} level={markerLevel} />}
              sum={group.totals}
              share={share(group.totals.total)}
              color={colorOf(group.key)}
            />
            {nested &&
              models(group).map((model) => (
                <UsageCard
                  key={model.key}
                  sub
                  title={model.key}
                  markers={<Markers rows={model.rows} sum={model.totals} level="model" />}
                  sum={model.totals}
                  share={share(model.totals.total)}
                  color={colorOf(group.key)}
                />
              ))}
          </Fragment>
        ))}
      </div>
    );
  }

  const cells = (sum: UsageTotals, color: string) => (
    <>
      <td className="px-3 py-2 text-right">
        {fmt(sum.requests)}
        {sum.withoutUsage > 0 && (
          <span className="ml-1.5 whitespace-nowrap rounded-full border border-border px-1.5 text-[10.5px] text-muted-foreground">
            {fmt(sum.withoutUsage)} without counts
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-right">{fmt(sum.prompt)}</td>
      <td className="px-3 py-2 text-right">{fmt(sum.completion)}</td>
      <td className="px-3 py-2 text-right font-semibold">{fmt(sum.total)}</td>
      <td className="px-3 py-2 text-right">
        <span className="inline-flex items-center justify-end gap-2">
          {share(sum.total).toFixed(1)} %
          <span className="h-1.5 w-16 overflow-hidden rounded bg-muted">
            <span
              className="block h-full"
              style={{ width: `${share(sum.total)}%`, background: color }}
            />
          </span>
        </span>
      </td>
      <td className="px-3 py-2 text-right">{sum.avgTokens === null ? "–" : fmt(sum.avgTokens)}</td>
      <td className="px-3 py-2 text-right">{seconds(sum.avgMs)}</td>
      <td className={cn("px-3 py-2 text-right", sum.failed > 0 && "text-destructive")}>
        {fmt(sum.failed)}
      </td>
    </>
  );

  return (
    <Card className="overflow-x-auto py-0">
      <table className="w-full min-w-[760px] text-sm tabular-nums">
        <thead>
          <tr className="border-b border-border text-[11px] uppercase tracking-wide text-muted-foreground">
            <th className="px-3 py-2 text-left font-semibold">
              {{ connection: "Connection", model: "Model", feature: "Feature" }[groupBy]}
            </th>
            <th className="px-3 py-2 text-right font-semibold">Requests</th>
            <th className="px-3 py-2 text-right font-semibold">In</th>
            <th className="px-3 py-2 text-right font-semibold">Out</th>
            <th className="px-3 py-2 text-right font-semibold">Total</th>
            <th className="px-3 py-2 text-right font-semibold">Share</th>
            <th className="px-3 py-2 text-right font-semibold">Ø / request</th>
            <th className="px-3 py-2 text-right font-semibold">Ø duration</th>
            <th className="px-3 py-2 text-right font-semibold">Failed</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => {
            const isOpen = open.has(group.key);
            const color = colorOf(group.key);
            const name = (
              <span className="inline-flex items-center gap-2">
                {nested && (
                  <ChevronRight
                    aria-hidden="true"
                    className={cn(
                      "size-3.5 text-muted-foreground transition-transform",
                      isOpen && "rotate-90",
                    )}
                  />
                )}
                <i className="size-2.5 shrink-0 rounded-sm" style={{ background: color }} />
                <span>{labelOf(group)}</span>
                <Markers rows={group.rows} sum={group.totals} level={markerLevel} />
              </span>
            );
            return (
              <Fragment key={group.key}>
                <tr className="border-b border-border">
                  <td className="px-3 py-2">
                    {nested ? (
                      <button
                        type="button"
                        aria-expanded={isOpen}
                        onClick={() => onToggle(group.key)}
                        className="rounded text-left hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {name}
                      </button>
                    ) : (
                      name
                    )}
                  </td>
                  {cells(group.totals, color)}
                </tr>
                {nested &&
                  isOpen &&
                  models(group).map((model) => (
                    <tr
                      key={model.key}
                      className="border-b border-border bg-muted/20 text-[13px] text-muted-foreground"
                    >
                      <td className="py-2 pr-3 pl-12">
                        {model.key}
                        <Markers rows={model.rows} sum={model.totals} level="model" />
                      </td>
                      {cells(model.totals, color)}
                    </tr>
                  ))}
              </Fragment>
            );
          })}
          <tr className="font-semibold">
            <td className="px-3 py-2">Total</td>
            {cells(total, "var(--muted-foreground)")}
          </tr>
        </tbody>
      </table>
    </Card>
  );
}

function UsageCard({
  title,
  markers,
  sum,
  share,
  color,
  sub,
}: {
  title: string;
  markers: React.ReactNode;
  sum: UsageTotals;
  share: number;
  color: string;
  sub?: boolean;
}) {
  return (
    <Card className={cn("gap-1.5 px-3.5 py-3", sub && "ml-4 bg-muted/40")}>
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 flex-wrap items-center gap-y-1 text-sm">
          {!sub && (
            <i className="mr-2 size-2.5 shrink-0 rounded-sm" style={{ background: color }} />
          )}
          <span className="break-all">{title}</span>
          {markers}
        </span>
        <span className="font-semibold tabular-nums">{fmt(sum.total)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded bg-muted">
        <span className="block h-full" style={{ width: `${share}%`, background: color }} />
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs tabular-nums text-muted-foreground">
        <span>{share.toFixed(1)} %</span>
        <span>{fmt(sum.requests)} requests</span>
        <span>
          in {fmt(sum.prompt)} · out {fmt(sum.completion)}
        </span>
        {sum.avgTokens !== null && <span>Ø {fmt(sum.avgTokens)} / request</span>}
        {sum.avgMs !== null && <span>Ø {seconds(sum.avgMs)}</span>}
        {sum.failed > 0 && <span className="text-destructive">{fmt(sum.failed)} failed</span>}
        {sum.withoutUsage > 0 && <span>{fmt(sum.withoutUsage)} without counts</span>}
      </div>
    </Card>
  );
}
