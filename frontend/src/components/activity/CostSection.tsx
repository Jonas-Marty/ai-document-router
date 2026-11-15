import { Link } from "react-router-dom";
import { ErrorState } from "@/components/shared/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useAiCosts } from "@/hooks/useAiCosts";
import { COST_KINDS, type CostPeriod, comparisonFor, costBuckets, forecast } from "@/lib/aiCosts";
import { formatCost, formatTokens } from "@/lib/tokens";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api/errors";
import type { ConfigurationCost, CostReport } from "@/services/api/types";
import { CostChart, KIND_COLORS } from "./CostChart";
import { DocumentName, RecentAnalyses } from "./RecentAnalyses";

const count = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString("en")} ${n === 1 ? one : many}`;
const share = (part: number, whole: number) =>
  whole > 0 ? `${Math.round((part / whole) * 100)}%` : "–";

/** SPEC 8.11: what one analysis costs -- on average, per filed document, in total and at this
 * pace -- then where the money went, each analysis, each configuration, the dearest
 * documents. In the period's main currency; anything billed in another is listed apart. */
export function CostSection({ period }: { period: CostPeriod }) {
  const costs = useAiCosts(period.from, period.to);
  const comparison = comparisonFor(period);
  const previous = useAiCosts(
    comparison?.from ?? period.from,
    comparison?.to ?? period.to,
    comparison !== null,
  );

  return (
    <>
      <section aria-labelledby="costs-heading" className="space-y-5 rounded-xl border p-4 sm:p-5">
        <h2 id="costs-heading" className="text-base font-semibold">
          What an analysis costs
        </h2>
        {costs.isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : costs.isError ? (
          <ErrorState
            message={costs.error instanceof ApiError ? costs.error.message : "Couldn't load costs."}
            onRetry={() => costs.refetch()}
          />
        ) : (
          costs.data && (
            <Overview
              report={costs.data}
              period={period}
              previous={
                comparison && previous.data && previous.data.currency === costs.data.currency
                  ? { spent: previous.data.spent, label: comparison.label }
                  : null
              }
            />
          )
        )}
      </section>
      <RecentAnalyses period={period} />
      {costs.data && costs.data.analyses.count > 0 && (
        <div className="grid gap-6 md:grid-cols-2">
          <Configurations report={costs.data} />
          <Expensive report={costs.data} />
        </div>
      )}
    </>
  );
}

function Overview({
  report,
  period,
  previous,
}: {
  report: CostReport;
  period: CostPeriod;
  previous: { spent: number; label: string } | null;
}) {
  const money = (value: number) => formatCost(value, report.currency);
  if (report.analyses.count === 0 && !report.priced) {
    return (
      <p className="text-sm text-muted-foreground">No documents were analysed in this period.</p>
    );
  }
  if (!report.priced) {
    return (
      <p className="text-sm text-muted-foreground">
        {count(report.analyses.count, "analysis", "analyses")} in this period, none on a model with
        a price. Add prices to a connection under{" "}
        <Link to="/settings" className="underline underline-offset-4">
          Settings
        </Link>{" "}
        to see what they cost.
      </p>
    );
  }
  const { analyses, filed } = report;
  const pace = forecast(period, report.spent);
  const difference = previous ? report.spent - previous.spent : null;

  return (
    <>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
        <Figure
          label="One analysis, on average"
          value={analyses.average === null ? "–" : money(analyses.average)}
          sub={[
            analyses.minimum !== null && analyses.maximum !== null && analyses.priced > 1
              ? `${money(analyses.minimum)} – ${money(analyses.maximum)}`
              : null,
            count(analyses.count, "analysis", "analyses"),
            analyses.priced < analyses.count
              ? `${analyses.count - analyses.priced} without a price`
              : null,
          ]}
        />
        <Figure
          label="One filed document"
          value={filed.average === null ? "–" : money(filed.average)}
          sub={[
            report.filed_documents > 0
              ? `${count(report.filed_documents, "document")} filed, re-analyses and failed attempts included`
              : "nothing filed in this period",
          ]}
        />
        <Figure
          label={period.kind === "month" ? "Spent so far" : "Spent"}
          value={money(report.spent)}
          sub={[
            difference === null || !previous
              ? null
              : `${difference >= 0 ? "▲" : "▼"} ${money(Math.abs(difference))} vs ${previous.label}`,
          ]}
        />
        {pace !== null && (
          <Figure
            label="At this pace, by month end"
            value={`≈ ${money(pace)}`}
            sub={["if the rest of the month goes like the days so far"]}
            muted
          />
        )}
      </dl>

      <CostChart buckets={costBuckets(report.days, period)} currency={report.currency} />

      <ul aria-label="Where it went" className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {COST_KINDS.map(({ kind, label }) => (
          <li
            key={kind}
            className={cn(
              "rounded-lg border px-3 py-2.5",
              kind === "failed" &&
                report.split.failed > 0 &&
                "border-orange-200 bg-orange-50 dark:border-orange-900 dark:bg-orange-950/40",
            )}
          >
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span aria-hidden="true" className={cn("size-2.5 rounded-sm", KIND_COLORS[kind])} />
              {kind === "failed" ? "Failed, no proposal" : label}
            </span>
            <span className="mt-0.5 block font-semibold tabular-nums">
              {money(report.split[kind])} · {share(report.split[kind], report.spent)}
            </span>
          </li>
        ))}
      </ul>

      {report.others.length > 0 && (
        <p className="rounded-lg border bg-muted/40 px-3 py-2.5 text-sm text-muted-foreground">
          <span className="font-medium text-foreground">
            Also{" "}
            {report.others.map((other) => formatCost(other.amount, other.currency)).join(" + ")}
          </span>{" "}
          in another currency, shown apart: amounts in two currencies are never added.
        </p>
      )}
    </>
  );
}

function Figure({
  label,
  value,
  sub,
  muted,
}: {
  label: string;
  value: string;
  sub: (string | null)[];
  muted?: boolean;
}) {
  const lines = sub.filter((line): line is string => line !== null);
  return (
    <div className="space-y-1">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "text-3xl font-semibold tracking-tight tabular-nums",
          muted && "text-foreground/75",
        )}
      >
        {value}
      </dd>
      {lines.length > 0 && <dd className="text-sm text-muted-foreground">{lines.join(" · ")}</dd>}
    </div>
  );
}

function configurationLine(row: ConfigurationCost, currency: string | null): string {
  return [
    count(row.analyses, "analysis", "analyses"),
    row.cost !== null ? formatCost(row.cost, currency) : `${formatTokens(row.tokens)} tokens`,
    row.failed > 0 ? `${row.failed} failed` : null,
    row.approvals > 0
      ? `${share(row.approved_unchanged, row.approvals)} of ${row.approvals} approved unchanged`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function Configurations({ report }: { report: CostReport }) {
  const top = Math.max(...report.configurations.map((row) => row.cost ?? 0), 0);
  return (
    <section aria-labelledby="configuration-costs-heading" className="min-w-0 space-y-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="configuration-costs-heading" className="text-sm font-medium">
          By configuration
        </h2>
        <Link to="/lab" className="text-sm underline-offset-4 hover:underline">
          Compare in the Lab →
        </Link>
      </div>
      <ul className="divide-y rounded-lg border text-sm">
        {report.configurations.map((row) => (
          <li key={row.configuration_id ?? row.name} className="space-y-1.5 px-3 py-2.5">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <span className="min-w-0 break-words">
                {row.name}
                {row.is_default && (
                  <Badge variant="secondary" className="ml-1.5">
                    Default
                  </Badge>
                )}
              </span>
              {row.average !== null ? (
                <strong className="font-semibold tabular-nums">
                  {formatCost(row.average, report.currency)} / analysis
                </strong>
              ) : (
                <span className="text-muted-foreground">no price</span>
              )}
            </div>
            {row.cost !== null && top > 0 && (
              <div aria-hidden="true" className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full bg-blue-700 dark:bg-blue-500"
                  style={{ width: `${(row.cost / top) * 100}%` }}
                />
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              {configurationLine(row, report.currency)}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Expensive({ report }: { report: CostReport }) {
  return (
    <section aria-labelledby="expensive-heading" className="min-w-0 space-y-2.5">
      <h2 id="expensive-heading" className="text-sm font-medium">
        Most expensive documents
      </h2>
      {report.expensive.length === 0 ? (
        <p className="text-sm text-muted-foreground">No analysis in this period had a price.</p>
      ) : (
        <ol className="divide-y rounded-lg border text-sm">
          {report.expensive.map((document) => (
            <li key={document.document_id} className="space-y-0.5 px-3 py-2.5">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <DocumentName id={document.document_id} name={document.name} open={document.open} />
                <strong className="font-semibold tabular-nums">
                  {formatCost(document.cost, report.currency)}
                </strong>
              </div>
              <p className="text-xs text-muted-foreground">
                {[
                  count(document.attempts, "attempt"),
                  document.failed > 0 ? `${document.failed} failed` : null,
                  document.page_count ? count(document.page_count, "page") : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
