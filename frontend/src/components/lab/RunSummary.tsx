import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useIsDesktop } from "@/hooks/useBreakpoint";
import { formatCost, formatTokens } from "@/lib/tokens";
import { cn } from "@/lib/utils";
import type { LabConfigurationSummary, LabField } from "@/services/api/types";
import { FIELD_LABELS, formatSeconds, formatShare, scoredFields } from "./labFormat";

interface Column {
  key: string;
  label: string;
  value: (summary: LabConfigurationSummary) => number | null | undefined;
  format: (value: number | null | undefined) => string;
  better: "higher" | "lower";
}

function tokens(summary: LabConfigurationSummary): number {
  return summary.prompt_tokens + summary.completion_tokens;
}

function columnsFor(fields: LabField[]): Column[] {
  const share = (value: number | null | undefined) => formatShare(value ?? undefined);
  return [
    ...fields.map(
      (field): Column => ({
        key: field,
        label: FIELD_LABELS[field],
        value: (summary) => summary.accuracy[field],
        format: share,
        better: "higher",
      }),
    ),
    {
      key: "fully_correct",
      label: "Fully correct",
      value: (summary) => summary.fully_correct,
      format: share,
      better: "higher",
    },
    {
      key: "time",
      label: "Time / doc",
      value: (summary) => summary.average_ms,
      format: (value) => formatSeconds(value ?? null),
      better: "lower",
    },
    {
      key: "tokens",
      label: "Tokens",
      value: (summary) => (summary.finished ? tokens(summary) : null),
      format: (value) => (value == null ? "–" : formatTokens(value)),
      better: "lower",
    },
    {
      key: "cost",
      label: "Cost",
      value: (summary) => summary.cost,
      format: (value) => (value == null ? "–" : formatCost(value)),
      better: "lower",
    },
  ];
}

/** The best value of a column, among configurations that have finished something: an empty
 * row is not the cheapest. Nothing is best when only one configuration ran. */
function bestOf(column: Column, summaries: LabConfigurationSummary[]): number | null {
  if (summaries.length < 2) return null;
  const values = summaries
    .filter((summary) => summary.finished > 0)
    .map(column.value)
    .filter((value): value is number => typeof value === "number");
  if (!values.length) return null;
  return column.better === "higher" ? Math.max(...values) : Math.min(...values);
}

/** SPEC 8.13a: a row per configuration, best value per column in bold. Phone: a card each. */
export function RunSummary({ summaries }: { summaries: LabConfigurationSummary[] }) {
  const isDesktop = useIsDesktop();
  const columns = columnsFor(scoredFields(summaries));
  const best = new Map(columns.map((column) => [column.key, bestOf(column, summaries)]));
  const isBest = (column: Column, summary: LabConfigurationSummary) => {
    const value = column.value(summary);
    return summary.finished > 0 && value != null && value === best.get(column.key);
  };

  if (!isDesktop) {
    return (
      <ul className="space-y-3">
        {summaries.map((summary) => (
          <li key={summary.configuration_id} className="rounded-lg border p-3">
            <h3 className="font-medium break-words">{summary.configuration_name}</h3>
            {summary.failed > 0 && (
              <p className="text-xs text-muted-foreground">{summary.failed} failed</p>
            )}
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              {columns.map((column) => (
                <div key={column.key} className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">{column.label}</dt>
                  <dd className={cn("tabular-nums", isBest(column, summary) && "font-semibold")}>
                    {column.format(column.value(summary))}
                  </dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Configuration</TableHead>
          {columns.map((column) => (
            <TableHead key={column.key} className="text-right">
              {column.label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {summaries.map((summary) => (
          <TableRow key={summary.configuration_id}>
            <TableCell className="max-w-56 whitespace-normal break-words">
              {summary.configuration_name}
              {summary.failed > 0 && (
                <span className="block text-xs text-muted-foreground">{summary.failed} failed</span>
              )}
            </TableCell>
            {columns.map((column) => (
              <TableCell
                key={column.key}
                className={cn(
                  "text-right tabular-nums",
                  isBest(column, summary) && "font-semibold",
                )}
              >
                {column.format(column.value(summary))}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
