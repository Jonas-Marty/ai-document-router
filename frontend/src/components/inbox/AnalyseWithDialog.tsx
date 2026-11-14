import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useConfigurations } from "@/hooks/useAiConfig";
import { useAnalysisEstimate, useEndpointStatuses } from "@/hooks/useInbox";
import { formatCost, formatTokens } from "@/lib/tokens";
import { SHRINKING_SELECT_TRIGGER } from "@/lib/utils";
import { ApiError } from "@/services/api/errors";
import type { AnalysisEstimate } from "@/services/api/types";

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function EstimateSummary({ estimate }: { estimate: AnalysisEstimate }) {
  const statuses = useEndpointStatuses().data ?? [];
  const tokens =
    estimate.prompt_tokens === null || estimate.completion_tokens === null
      ? "No past calls with these models to estimate tokens from."
      : `About ${formatTokens(estimate.prompt_tokens)} in · ${formatTokens(estimate.completion_tokens)} out.`;
  const cost =
    estimate.cost !== null
      ? `Roughly ${formatCost(estimate.cost)}.`
      : estimate.prompt_tokens !== null
        ? "No price set for these models."
        : null;

  return (
    <dl className="space-y-1 text-sm">
      <div>
        <dt className="sr-only">Documents</dt>
        <dd>
          {plural(estimate.documents, "document", "documents")}
          {estimate.readings_needed > 0 &&
            `, ${plural(estimate.readings_needed, "needs", "need")} a new reading`}
          .
        </dd>
      </div>
      <div>
        <dt className="sr-only">Tokens</dt>
        <dd className="text-muted-foreground">{tokens}</dd>
      </div>
      {cost && (
        <div>
          <dt className="sr-only">Cost</dt>
          <dd className="text-muted-foreground">{cost}</dd>
        </div>
      )}
      {estimate.connections.length > 0 && (
        <div>
          <dt className="sr-only">Connections</dt>
          <dd className="flex flex-wrap gap-x-3 gap-y-1">
            {estimate.connections.map((name) => {
              const status = statuses.find((row) => row.name === name);
              return (
                <span key={name} className="inline-flex items-center gap-1.5">
                  <span
                    aria-hidden="true"
                    className={`size-2 rounded-full ${status?.reachable ? "bg-emerald-500" : "bg-muted-foreground/40"}`}
                  />
                  {name}
                  {status && !status.reachable && (
                    <span className="text-muted-foreground">(unreachable — documents wait)</span>
                  )}
                </span>
              );
            })}
          </dd>
        </div>
      )}
    </dl>
  );
}

/** SPEC 8.6: analyse the selected documents with a chosen configuration, after seeing what
 * that would roughly cost. The default is preselected. */
export function AnalyseWithDialog({
  documentIds,
  open,
  onOpenChange,
  isPending,
  onRun,
}: {
  documentIds: string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isPending: boolean;
  onRun: (configurationId: string) => void;
}) {
  const configurations = useConfigurations();
  const list = configurations.data ?? [];
  const defaultId = list.find((row) => row.is_default)?.id ?? "";
  const [configurationId, setConfigurationId] = useState("");

  useEffect(() => {
    if (open) setConfigurationId(defaultId);
  }, [open, defaultId]);

  const estimate = useAnalysisEstimate(
    open ? documentIds : [],
    configurationId === "" ? undefined : configurationId,
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Analyse with…</DialogTitle>
          <DialogDescription>
            {plural(documentIds.length, "document", "documents")} selected. Each keeps the
            configuration chosen here for later re-analyses.
          </DialogDescription>
        </DialogHeader>

        <div className="min-w-0 space-y-1.5">
          <Label htmlFor="analyse-configuration">Configuration</Label>
          <Select value={configurationId} onValueChange={setConfigurationId}>
            <SelectTrigger id="analyse-configuration" className={SHRINKING_SELECT_TRIGGER}>
              <SelectValue placeholder="Choose a configuration" />
            </SelectTrigger>
            <SelectContent>
              {list.map((row) => (
                <SelectItem key={row.id} value={row.id}>
                  {row.is_default ? `${row.name} (default)` : row.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {configurations.isSuccess && list.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No configurations yet —{" "}
              <Link to="/lab/configurations" className="underline underline-offset-4">
                create one in the Lab
              </Link>
              .
            </p>
          )}
        </div>

        <section aria-label="Estimate" aria-busy={estimate.isFetching} className="min-h-16">
          {estimate.isError ? (
            <p className="text-sm text-destructive">
              {estimate.error instanceof ApiError
                ? estimate.error.message
                : "Couldn't estimate the cost."}
            </p>
          ) : estimate.data ? (
            <EstimateSummary estimate={estimate.data} />
          ) : estimate.isFetching ? (
            <p className="text-sm text-muted-foreground">Estimating…</p>
          ) : null}
        </section>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={configurationId === "" || isPending}
            onClick={() => onRun(configurationId)}
          >
            {isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
            Analyse
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
