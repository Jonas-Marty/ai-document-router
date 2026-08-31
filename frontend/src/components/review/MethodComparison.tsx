import { Check, CircleAlert, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useIsDesktop } from "@/hooks/useBreakpoint";
import type { MethodResult } from "@/services/api/types";
import { ConfidenceBadge } from "./ConfidenceBadge";

export interface MethodComparisonProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  extension: string;
  results: MethodResult[] | undefined;
  isPending: boolean;
  error: string | null;
  onUse: (result: MethodResult) => void;
}

/** Every configured way of reading this document, side by side.
 *
 * There is no scoring and no winner: the methods disagree about things only the person
 * filing the document can settle, so this shows what each one said and lets them choose.
 * Picking one fills the review form -- it does not approve anything, and the choice stays
 * editable, because "nearly right" is the common case and the whole point of a review step.
 */
export function MethodComparison({
  open,
  onOpenChange,
  extension,
  results,
  isPending,
  error,
  onUse,
}: MethodComparisonProps) {
  const isDesktop = useIsDesktop();

  const body = (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 pt-0">
      {isPending ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground" aria-busy="true">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Reading this document every way — one model call per method, so this takes a moment.
        </p>
      ) : error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : results && results.length > 0 ? (
        results.map((result) => (
          <MethodCard
            key={`${result.method}:${result.model_name}`}
            result={result}
            extension={extension}
            onUse={() => {
              onUse(result);
              onOpenChange(false);
            }}
          />
        ))
      ) : (
        <p className="text-sm text-muted-foreground">Nothing to compare yet.</p>
      )}
    </div>
  );

  const heading = "Compare methods";

  if (isDesktop) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="flex h-[36rem] max-w-2xl flex-col gap-0 p-0">
          <DialogHeader className="p-4">
            <DialogTitle>{heading}</DialogTitle>
          </DialogHeader>
          {body}
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        className="flex flex-col gap-0 rounded-none p-0 data-[side=bottom]:h-dvh"
      >
        <SheetHeader className="p-4">
          <SheetTitle>{heading}</SheetTitle>
        </SheetHeader>
        {body}
      </SheetContent>
    </Sheet>
  );
}

function MethodCard({
  result,
  extension,
  onUse,
}: {
  result: MethodResult;
  extension: string;
  onUse: () => void;
}) {
  const { proposal } = result;

  return (
    <section className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">{result.label}</h3>
        <div className="flex items-center gap-2">
          {proposal && <ConfidenceBadge score={proposal.confidence_score} />}
          {/* Seconds, not milliseconds: this is a judgement about whether a method is worth
              waiting for, and 4.2 s answers that where 4231 ms just has to be divided. */}
          {result.duration_ms > 0 && (
            <Badge variant="outline">{(result.duration_ms / 1000).toFixed(1)}s</Badge>
          )}
        </div>
      </div>

      {proposal ? (
        <>
          <dl className="mt-2 space-y-1 text-sm">
            <div className="flex gap-2">
              <dt className="w-20 shrink-0 text-muted-foreground">Name</dt>
              <dd className="min-w-0 flex-1 break-words font-mono">
                {proposal.suggested_name}
                {extension}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-20 shrink-0 text-muted-foreground">Folder</dt>
              <dd className="min-w-0 flex-1 break-words font-mono">
                {proposal.target_folder_path}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-20 shrink-0 text-muted-foreground">Date</dt>
              <dd className="min-w-0 flex-1 font-mono">{proposal.document_date ?? "—"}</dd>
            </div>
          </dl>
          {proposal.reasoning_text && (
            <p className="mt-2 text-sm text-muted-foreground">{proposal.reasoning_text}</p>
          )}
          <Button size="sm" variant="outline" className="mt-3" onClick={onUse}>
            <Check className="size-4" aria-hidden="true" />
            Use this
          </Button>
        </>
      ) : (
        <p className="mt-2 flex items-start gap-1.5 text-sm text-destructive">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span className="min-w-0 flex-1">{result.error ?? "This method produced nothing."}</span>
        </p>
      )}
    </section>
  );
}
