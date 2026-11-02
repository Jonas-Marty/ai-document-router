import { ChevronDown, Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";
import { PromptBlock } from "@/components/review/PromptDetails";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAiContext } from "@/hooks/useAiConfig";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api/errors";
import type { AiTask, AiTaskContext } from "@/services/api/types";

// The same names the AI tasks cards use, so the two sections read as describing one thing.
const TASK_TITLES: Record<AiTask, string> = {
  extraction: "Extraction — read the pages",
  filing: "Filing — choose the filename",
};

/** SPEC 8.7: what each AI task is sent right now, built from the saved settings with a
 * placeholder where the document goes. The review screen shows what was sent for one
 * proposal; this is how to check an edit to folders, the naming hint or the instructions
 * without waiting for a scan.
 *
 * Collapsed, and fetched only once opened: building it walks the WebDAV folder tree. Not a
 * form, so it takes no part in the page's dirty tracking. */
export function AiContextSection() {
  const [open, setOpen] = useState(false);
  const context = useAiContext(open);

  return (
    <Card>
      <CardHeader>
        <button
          type="button"
          className="flex w-full items-center justify-between gap-2 text-left"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          <CardTitle>What the models are given</CardTitle>
          <ChevronDown
            className={cn("size-4 shrink-0 transition-transform", open && "rotate-180")}
            aria-hidden="true"
          />
        </button>
      </CardHeader>
      {open && (
        <CardContent className="space-y-4">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              Built from your saved settings, exactly as the next document will be sent. Unsaved
              edits above are not included.
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="shrink-0"
              disabled={context.isFetching}
              onClick={() => context.refetch()}
            >
              {context.isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Refresh
            </Button>
          </div>
          {context.isPending ? (
            <div className="space-y-2" aria-busy="true">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          ) : context.isError ? (
            <ErrorState
              message={
                context.error instanceof ApiError
                  ? context.error.message
                  : "Couldn't load what the models are given."
              }
              onRetry={() => context.refetch()}
            />
          ) : (
            context.data.map((task) => <TaskContextView key={task.task} context={task} />)
          )}
        </CardContent>
      )}
    </Card>
  );
}

function TaskContextView({ context }: { context: AiTaskContext }) {
  return (
    <section className="space-y-2 border-t border-border pt-4 first-of-type:border-t-0 first-of-type:pt-0">
      <h3 className="text-sm font-semibold">{TASK_TITLES[context.task]}</h3>
      <p className="text-sm break-words">
        <span className="text-muted-foreground">Tried: </span>
        {context.chain.length > 0 ? (
          context.chain.join(" → ")
        ) : (
          <span className="text-muted-foreground">no endpoint assigned yet</span>
        )}
      </p>
      <p className="text-sm text-muted-foreground">{context.note}</p>
      <PromptBlock label="Instructions" text={context.system_prompt} />
      <PromptBlock label="Message" text={context.user_prompt} />
    </section>
  );
}
