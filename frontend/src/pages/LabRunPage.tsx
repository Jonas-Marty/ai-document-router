import { ArrowLeft, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { LabTabs } from "@/components/lab/LabTabs";
import { configurationNames, isLabField } from "@/components/lab/labFormat";
import { RunProgress } from "@/components/lab/RunProgress";
import { RunSummary } from "@/components/lab/RunSummary";
import { VerdictBadge } from "@/components/lab/VerdictBadge";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useCancelLabRun, useDeleteLabRun, useLabRun, useSetLabVerdict } from "@/hooks/useLab";
import { absoluteTime } from "@/lib/relativeTime";
import { ApiError } from "@/services/api/errors";
import type { LabField, LabResult } from "@/services/api/types";

function failed(fallback: string) {
  return (error: Error) => toast.error(error instanceof ApiError ? error.message : fallback);
}

/** SPEC 8.13a: one run -- its progress, the scores per configuration, and every document with
 * each configuration's answer and verdicts. */
export default function LabRunPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const run = useLabRun(id);
  const cancel = useCancelLabRun();
  const remove = useDeleteLabRun();
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  let body: React.ReactNode;
  if (run.isLoading) {
    body = (
      <div className="space-y-3" aria-busy="true">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  } else if (run.isError || !run.data) {
    const notFound = run.error instanceof ApiError && run.error.status === 404;
    body = (
      <ErrorState
        message={
          notFound
            ? "This run no longer exists."
            : run.error instanceof ApiError
              ? run.error.message
              : "Couldn't load the run."
        }
        onRetry={notFound ? undefined : () => run.refetch()}
      />
    );
  } else {
    const data = run.data;
    body = (
      <>
        <div className="space-y-2">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <h2 className="text-base font-semibold break-words">{data.test_set_name}</h2>
              <p className="text-sm break-words text-muted-foreground">
                {configurationNames(data)} · {absoluteTime(data.created_at)}
              </p>
            </div>
            {data.status === "running" ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={cancel.isPending}
                onClick={() =>
                  cancel.mutate(data.id, { onError: failed("Couldn't cancel the run.") })
                }
              >
                Cancel run
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setConfirmingDelete(true)}
              >
                Delete
              </Button>
            )}
          </div>
          <RunProgress run={data} showStage />
        </div>
        <section aria-labelledby="run-scores" className="space-y-2">
          <h2 id="run-scores" className="text-sm font-medium">
            Scores
          </h2>
          <RunSummary summaries={data.summaries} />
        </section>
        <section aria-labelledby="run-documents" className="space-y-3">
          <h2 id="run-documents" className="text-sm font-medium">
            Documents
          </h2>
          <DocumentResults runId={data.id} results={data.results} />
        </section>
      </>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <div className="space-y-3">
        <h1 className="text-lg font-semibold">Lab</h1>
        <LabTabs />
      </div>
      <Link
        to="/lab"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Runs
      </Link>
      {body}

      <Dialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this run?</DialogTitle>
            <DialogDescription>
              Its scores and your verdicts go with it. The test set stays.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmingDelete(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={remove.isPending}
              onClick={() =>
                remove.mutate(id, {
                  onSuccess: () => {
                    toast.success("Deleted the run");
                    navigate("/lab");
                  },
                  onError: failed("Couldn't delete the run."),
                  onSettled: () => setConfirmingDelete(false),
                })
              }
            >
              {remove.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** One block per document, in the set's order; inside it one row per configuration. */
function DocumentResults({ runId, results }: { runId: string; results: LabResult[] }) {
  const byCase = new Map<string, LabResult[]>();
  for (const result of results) {
    byCase.set(result.test_case_id, [...(byCase.get(result.test_case_id) ?? []), result]);
  }
  return (
    <ul className="space-y-3">
      {[...byCase.entries()].map(([caseId, rows]) => {
        const first = rows[0];
        if (!first) return null;
        return (
          <li key={caseId} className="space-y-2 rounded-lg border p-3">
            <h3 className="font-mono text-sm break-words">{first.original_filename}</h3>
            <dl className="grid gap-x-3 text-sm sm:grid-cols-[auto_1fr]">
              <dt className="text-muted-foreground">Correct</dt>
              <dd className="min-w-0 font-mono text-xs break-words sm:text-sm">
                {first.expected_name}
              </dd>
              <dt className="sr-only">Correct folder</dt>
              <dd className="min-w-0 font-mono text-xs break-words text-muted-foreground sm:col-start-2 sm:text-sm">
                {first.expected_folder}
              </dd>
            </dl>
            <ul className="divide-y">
              {rows.map((result) => (
                <ResultRow key={result.id} runId={runId} result={result} />
              ))}
            </ul>
          </li>
        );
      })}
    </ul>
  );
}

function ResultRow({ runId, result }: { runId: string; result: LabResult }) {
  const setVerdict = useSetLabVerdict(runId);
  const fields = Object.keys(result.verdicts).filter(isLabField);

  function change(field: LabField, score: number | null) {
    setVerdict.mutate(
      { id: result.id, field, score },
      { onError: failed("Couldn't change the verdict.") },
    );
  }

  return (
    <li className="space-y-1.5 py-2 first:pt-0 last:pb-0">
      <p className="text-sm font-medium break-words">{result.configuration_name}</p>
      {result.status === "pending" && <p className="text-sm text-muted-foreground">Waiting…</p>}
      {result.status === "cancelled" && <p className="text-sm text-muted-foreground">Cancelled</p>}
      {result.status === "failed" && (
        <p className="text-sm break-words text-muted-foreground">Failed: {result.error}</p>
      )}
      {result.status === "done" && (
        <div className="space-y-0.5">
          <p className="font-mono text-xs break-words sm:text-sm">{result.predicted_name}</p>
          <p className="font-mono text-xs break-words text-muted-foreground sm:text-sm">
            {result.predicted_folder}
          </p>
        </div>
      )}
      {fields.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {fields.map((field) => (
            <VerdictBadge
              key={field}
              field={field}
              automatic={result.verdicts[field] ?? 0}
              override={result.overrides[field]}
              disabled={setVerdict.isPending}
              onChange={(score) => change(field, score)}
            />
          ))}
        </div>
      )}
    </li>
  );
}
