import { FlaskConical, Play } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { LabHeader } from "@/components/lab/LabTabs";
import { configurationNames } from "@/components/lab/labFormat";
import { NewRunDialog } from "@/components/lab/NewRunDialog";
import { RunProgress } from "@/components/lab/RunProgress";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useLabRuns } from "@/hooks/useLab";
import { relativeTime } from "@/lib/relativeTime";
import { ApiError } from "@/services/api/errors";

/** SPEC 8.13a: the runs, newest first, each with its progress. */
export default function LabRunsPage() {
  const runs = useLabRuns();
  const [newRunOpen, setNewRunOpen] = useState(false);

  let body: React.ReactNode;
  if (runs.isLoading) {
    body = (
      <div className="space-y-3" aria-busy="true">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  } else if (runs.isError) {
    body = (
      <ErrorState
        message={runs.error instanceof ApiError ? runs.error.message : "Couldn't load the runs."}
        onRetry={() => runs.refetch()}
      />
    );
  } else if (!runs.data?.length) {
    body = (
      <EmptyState
        icon={FlaskConical}
        title="No runs yet"
        description="A run files every document of a test set with the configurations you pick and scores each against the answer it was filed with."
        action={{ label: "New run", onClick: () => setNewRunOpen(true) }}
      />
    );
  } else {
    body = (
      <ul className="space-y-3">
        {runs.data.map((run) => (
          <li key={run.id}>
            <Card className="relative transition-colors hover:bg-accent/40">
              <CardContent className="space-y-2">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <h2 className="min-w-0 font-medium break-words">
                    {/* The whole card is the link; the stretched ::after keeps one tab stop. */}
                    <Link
                      to={`/lab/runs/${run.id}`}
                      className="after:absolute after:inset-0 after:rounded-xl focus-visible:outline-none focus-visible:after:ring-[3px] focus-visible:after:ring-ring/50"
                    >
                      {run.test_set_name}
                    </Link>
                  </h2>
                  <span className="text-xs text-muted-foreground">
                    {relativeTime(run.created_at)}
                  </span>
                </div>
                <p className="text-sm break-words text-muted-foreground">
                  {configurationNames(run)}
                </p>
                <RunProgress run={run} />
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4">
      <LabHeader
        action={
          (runs.data?.length ?? 0) > 0 && (
            <Button size="sm" onClick={() => setNewRunOpen(true)}>
              <Play aria-hidden="true" />
              New run
            </Button>
          )
        }
      />
      {body}
      <NewRunDialog open={newRunOpen} onOpenChange={setNewRunOpen} />
    </div>
  );
}
