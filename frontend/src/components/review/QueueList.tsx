import { CircleAlert, Loader2, RotateCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Document } from "@/services/api/types";

export interface QueueListProps {
  items: Document[];
  totalPending: number;
  currentId: string | undefined;
  onSelect: (id: string) => void;
  onRetryFailed: () => void;
  isRetryingFailed: boolean;
}

/** Everything still waiting to be filed, in the order the review screen will reach it.
 *
 * Each row is labelled with what the document will be *called*, not what it is called now:
 * a scanner names every file "scan_0041.pdf", so the original filename is exactly the thing
 * that cannot tell two of them apart. The original is the fallback only while there is no
 * proposal to name the row by yet. */
export function QueueList({
  items,
  totalPending,
  currentId,
  onSelect,
  onRetryFailed,
  isRetryingFailed,
}: QueueListProps) {
  const notLoaded = totalPending - items.length;
  const hasFailures = items.some((document) => document.proposal_status === "failed");

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {hasFailures && (
        // Most of these failures are one configuration problem wearing many hats -- no
        // allowed folders, a rejected AI request -- so the useful action after fixing it is
        // "do all of them again", not opening each document to press Try again. Offered
        // here because this list is where the failures are actually legible.
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/40 px-4 py-3">
          <p className="text-xs text-muted-foreground">
            Fixed the cause in Settings? Ask for these proposals again.
          </p>
          <Button size="sm" variant="outline" disabled={isRetryingFailed} onClick={onRetryFailed}>
            {isRetryingFailed ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <RotateCw className="size-4" aria-hidden="true" />
            )}
            Retry failed
          </Button>
        </div>
      )}
      <ul className="divide-y divide-border">
        {items.map((document) => {
          const isCurrent = document.id === currentId;
          return (
            <li key={document.id}>
              <button
                type="button"
                // aria-current, not aria-selected: this is a set of jumps to a position in a
                // queue, not a listbox of values being chosen between.
                aria-current={isCurrent ? "true" : undefined}
                onClick={() => onSelect(document.id)}
                className={cn(
                  "flex min-h-11 w-full flex-col items-start gap-1 px-4 py-3 text-left",
                  "hover:bg-muted focus-visible:bg-muted focus-visible:outline-none",
                  isCurrent && "bg-muted",
                )}
              >
                <div className="flex w-full items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-medium">{title(document)}</span>
                  {isCurrent && <Badge variant="secondary">Reviewing</Badge>}
                  {document.status === "skipped" && <Badge variant="outline">Skipped</Badge>}
                </div>
                <QueueRowStatus document={document} />
              </button>
            </li>
          );
        })}
      </ul>

      {notLoaded > 0 && (
        // The queue endpoint is capped at QUEUE_LIMIT, so on a big backlog this list is a
        // window onto the front of it rather than the whole thing. Saying so beats a count
        // in the header that the rows visibly do not add up to.
        <p className="px-4 py-3 text-xs text-muted-foreground">
          …and {notLoaded} more behind these. They appear as you work through the queue.
        </p>
      )}
    </div>
  );
}

function title(document: Document): string {
  return document.proposal
    ? `${document.proposal.suggested_name}${document.extension}`
    : document.original_filename;
}

/** Why a row is or is not ready to approve, in the same words the review form uses. */
function QueueRowStatus({ document }: { document: Document }) {
  if (document.proposal_status === "pending") {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="size-3 shrink-0 animate-spin" aria-hidden="true" />
        Waiting for the AI proposal…
      </span>
    );
  }

  if (document.proposal_status === "failed") {
    return (
      <span className="flex w-full items-start gap-1.5 text-xs text-destructive">
        <CircleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          {document.proposal_error ?? "The AI proposal failed."}
        </span>
      </span>
    );
  }

  return (
    <span className="w-full truncate font-mono text-xs text-muted-foreground">
      {document.proposal?.target_folder_path ?? document.original_filename}
    </span>
  );
}
