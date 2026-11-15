import { Loader2 } from "lucide-react";
import { Link } from "react-router-dom";
import { ErrorState } from "@/components/shared/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAiCostAnalyses } from "@/hooks/useAiCosts";
import { useIsDesktop } from "@/hooks/useBreakpoint";
import type { CostPeriod } from "@/lib/aiCosts";
import { absoluteTime, relativeTime } from "@/lib/relativeTime";
import { formatCost, formatTokens } from "@/lib/tokens";
import { ApiError } from "@/services/api/errors";
import type { AnalysisCost } from "@/services/api/types";

/** A document still in review opens there; a filed one has no page of its own to go to. */
export function DocumentName({
  id,
  name,
  open,
}: {
  id: string | null;
  name: string;
  open: boolean;
}) {
  if (id && open) {
    return (
      <Link to={`/review/${id}`} className="break-words underline-offset-4 hover:underline">
        {name}
      </Link>
    );
  }
  return <span className="break-words">{name}</span>;
}

function KindBadge({ kind }: { kind: AnalysisCost["kind"] }) {
  if (kind === "reanalysis") return <Badge variant="secondary">re-analysis</Badge>;
  if (kind === "failed") {
    return (
      <Badge variant="outline" className="border-orange-300 text-orange-800 dark:text-orange-300">
        failed
      </Badge>
    );
  }
  return null;
}

/** "CHF 0.0322 · CHF 0.0288": what reading and filing each cost; "free" for a step that ran
 * on an unpriced model, "–" for one that did not run (a reused reading, a failure before
 * filing). */
function steps(item: AnalysisCost): string {
  const part = (cost: number | null) => (cost === null ? "–" : formatCost(cost, item.currency));
  if (item.cost === null) return `${formatTokens(item.tokens)} tokens`;
  return `${part(item.reading_cost)} · ${part(item.filing_cost)}`;
}

function total(item: AnalysisCost): string {
  return item.cost === null ? "no price" : formatCost(item.cost, item.currency);
}

/** SPEC 8.11: each analysis of the period, as it was billed, newest first. */
export function RecentAnalyses({ period }: { period: CostPeriod }) {
  const analyses = useAiCostAnalyses(period.from, period.to);
  const isDesktop = useIsDesktop();
  const items = analyses.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section aria-labelledby="recent-analyses-heading" className="space-y-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="recent-analyses-heading" className="text-sm font-medium">
          Recent analyses
        </h2>
        <span className="text-xs text-muted-foreground">each one, as it was billed</span>
      </div>
      {analyses.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : analyses.isError ? (
        <ErrorState
          message={
            analyses.error instanceof ApiError ? analyses.error.message : "Couldn't load analyses."
          }
          onRetry={() => analyses.refetch()}
        />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No documents were analysed in this period.</p>
      ) : isDesktop ? (
        <div className="rounded-lg border">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-28">When</TableHead>
                <TableHead>Document</TableHead>
                <TableHead className="w-1/5">Configuration</TableHead>
                <TableHead className="w-48 text-right">Reading · filing</TableHead>
                <TableHead className="w-28 text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow key={item.run_id} className="align-top">
                  <TableCell
                    className="truncate text-muted-foreground"
                    title={absoluteTime(item.occurred_at)}
                  >
                    {relativeTime(item.occurred_at)}
                  </TableCell>
                  <TableCell className="space-x-1.5 whitespace-normal">
                    <DocumentName
                      id={item.document_id}
                      name={item.document_name}
                      open={item.document_open}
                    />
                    <KindBadge kind={item.kind} />
                  </TableCell>
                  <TableCell className="whitespace-normal break-words text-muted-foreground">
                    {item.configuration_name ?? "–"}
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground tabular-nums">
                    {steps(item)}
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {total(item)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <ul className="divide-y rounded-lg border">
          {items.map((item) => (
            <li key={item.run_id} className="space-y-1 px-3 py-2.5 text-sm">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="min-w-0 space-x-1.5">
                  <DocumentName
                    id={item.document_id}
                    name={item.document_name}
                    open={item.document_open}
                  />
                  <KindBadge kind={item.kind} />
                </span>
                <span className="font-semibold tabular-nums">{total(item)}</span>
              </div>
              <p className="text-xs text-muted-foreground">
                <time dateTime={item.occurred_at} title={absoluteTime(item.occurred_at)}>
                  {relativeTime(item.occurred_at)}
                </time>
                {item.configuration_name && ` · ${item.configuration_name}`}
                {` · ${steps(item)}`}
              </p>
            </li>
          ))}
        </ul>
      )}
      {analyses.hasNextPage && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => analyses.fetchNextPage()}
          disabled={analyses.isFetchingNextPage}
        >
          {analyses.isFetchingNextPage && <Loader2 className="animate-spin" />}
          Show more
        </Button>
      )}
    </section>
  );
}
