import { Clock } from "lucide-react";
import { Link } from "react-router-dom";
import { FLAG_LABELS, Thumbnail } from "@/components/inbox/InboxCardView";
import { InboxFilters } from "@/components/inbox/InboxFilters";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { useReviewList } from "@/hooks/useReviewList";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api/errors";

export interface ReviewListProps {
  review: ReturnType<typeof useReviewList>;
  /** Called after a row is picked, so the sheet it sits in can close. */
  onPick?: () => void;
}

/** SPEC 8.3 *List*: Ready for review as compact rows under the Inbox's filters, stacked for
 * a 320 px pane. A row is a link to its document; the URL is what makes it current. */
export function ReviewList({ review, onPick }: ReviewListProps) {
  const { filters, list, cards, currentId } = review;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-border p-3">
        <InboxFilters
          idPrefix="review"
          state={filters}
          senders={review.senders}
          folders={review.folders}
          stacked
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {list.isLoading ? (
          <div className="space-y-2 p-3" aria-busy="true">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : list.isError ? (
          <div className="p-3">
            <ErrorState
              message={
                list.error instanceof ApiError ? list.error.message : "Couldn't load the list."
              }
              onRetry={() => list.refetch()}
            />
          </div>
        ) : cards.length === 0 ? (
          <div className="p-3">
            {filters.filtered ? (
              <EmptyState
                title="Nothing matches."
                description="No document ready for review fits these filters."
                action={{ label: "Clear filters", onClick: filters.clear }}
              />
            ) : (
              <EmptyState title="Nothing ready." description="New proposals appear here." />
            )}
          </div>
        ) : (
          <>
            <ul aria-label="Ready for review" className="divide-y divide-border">
              {cards.map((card) => {
                const current = card.id === currentId;
                return (
                  <li key={card.id}>
                    <Link
                      to={`/review/${card.id}`}
                      aria-current={current ? "page" : undefined}
                      onClick={onPick}
                      className={cn(
                        "flex min-h-11 gap-3 px-3 py-2 transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                        current && "bg-accent",
                      )}
                    >
                      <Thumbnail id={card.id} className="w-10" />
                      <div className="min-w-0 flex-1 space-y-1">
                        <p className="line-clamp-2 font-mono text-sm break-words">
                          {card.name ?? card.original_filename}
                        </p>
                        {card.folder && (
                          <p className="truncate font-mono text-xs text-muted-foreground">
                            {card.folder}
                          </p>
                        )}
                        {(card.later || card.flags.length > 0) && (
                          <div className="flex flex-wrap gap-1">
                            {card.later && (
                              <Badge variant="secondary" className="gap-1">
                                <Clock className="size-3" aria-hidden="true" />
                                Later
                              </Badge>
                            )}
                            {card.flags.map((flag) => (
                              <Badge key={flag} variant="outline">
                                {FLAG_LABELS[flag]}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
            {list.hasNextPage && (
              <div className="p-3">
                <Button
                  type="button"
                  variant="outline"
                  className="w-full"
                  disabled={list.isFetchingNextPage}
                  onClick={() => list.fetchNextPage()}
                >
                  {list.isFetchingNextPage ? "Loading…" : "Load more"}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
