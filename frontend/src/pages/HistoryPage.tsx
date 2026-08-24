import { Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { HistoryCards } from "@/components/history/HistoryCards";
import { HistoryTable } from "@/components/history/HistoryTable";
import { RevertConfirmDialog } from "@/components/history/RevertConfirmDialog";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useIsDesktop } from "@/hooks/useBreakpoint";
import { useHistory, useRevertHistoryEntry } from "@/hooks/useHistory";
import { ApiError } from "@/services/api/errors";
import type { HistoryEntry } from "@/services/api/types";

export default function HistoryPage() {
  const history = useHistory();
  const isDesktop = useIsDesktop();
  const revertMutation = useRevertHistoryEntry();
  const [revertTarget, setRevertTarget] = useState<HistoryEntry | null>(null);

  const items = history.data?.pages.flatMap((page) => page.items) ?? [];

  function handleConfirmRevert() {
    if (!revertTarget) return;
    revertMutation.mutate(revertTarget.id, {
      onSuccess: () => {
        toast.success("Reverted — back in the queue");
        setRevertTarget(null);
      },
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.message : "Couldn't revert this file.");
        setRevertTarget(null);
      },
    });
  }

  return (
    <div className="mx-auto max-w-5xl p-4">
      <h1 className="text-lg font-semibold">History</h1>

      <div className="mt-4">
        {history.isLoading ? (
          <HistorySkeleton />
        ) : history.isError ? (
          <ErrorState
            message={
              history.error instanceof ApiError ? history.error.message : "Couldn't load history."
            }
            onRetry={() => history.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            title="Nothing filed yet."
            description="Approved and trashed documents show up here."
          />
        ) : (
          <>
            {isDesktop ? (
              <HistoryTable items={items} onRevert={setRevertTarget} />
            ) : (
              <HistoryCards items={items} onRevert={setRevertTarget} />
            )}
            {history.hasNextPage && (
              <div className="mt-4 flex justify-center">
                <Button
                  variant="outline"
                  onClick={() => history.fetchNextPage()}
                  disabled={history.isFetchingNextPage}
                >
                  {history.isFetchingNextPage && <Loader2 className="animate-spin" />}
                  Load more
                </Button>
              </div>
            )}
          </>
        )}
      </div>

      <RevertConfirmDialog
        entry={revertTarget}
        onOpenChange={(open) => !open && setRevertTarget(null)}
        onConfirm={handleConfirmRevert}
        isPending={revertMutation.isPending}
      />
    </div>
  );
}

function HistorySkeleton() {
  return (
    <div className="space-y-2" aria-busy="true">
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
    </div>
  );
}
