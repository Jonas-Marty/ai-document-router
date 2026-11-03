import { Loader2 } from "lucide-react";
import { useState } from "react";
import { AuditCards } from "@/components/activity/AuditCards";
import { AuditTable } from "@/components/activity/AuditTable";
import { UsageSummary } from "@/components/activity/UsageSummary";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuditEvents, useAuditUsage } from "@/hooks/useAudit";
import { useIsDesktop } from "@/hooks/useBreakpoint";
import { ApiError } from "@/services/api/errors";
import type { AuditCategory } from "@/services/api/types";

const FILTERS: { value: AuditCategory | undefined; label: string }[] = [
  { value: undefined, label: "All" },
  { value: "documents", label: "Documents" },
  { value: "ai", label: "AI" },
  { value: "settings", label: "Settings" },
  { value: "auth", label: "Sign-ins" },
];

/** SPEC 8.11: the audit log. Every account sees its own: what happened to its documents,
 * its settings, its sign-ins -- never anyone else's, admins included. */
export default function ActivityPage() {
  const [category, setCategory] = useState<AuditCategory | undefined>(undefined);
  const events = useAuditEvents(category);
  const usage = useAuditUsage();
  const isDesktop = useIsDesktop();

  const items = events.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4">
      <h1 className="text-lg font-semibold">Activity</h1>

      {usage.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : usage.isError ? (
        <ErrorState
          message={usage.error instanceof ApiError ? usage.error.message : "Couldn't load usage."}
          onRetry={() => usage.refetch()}
        />
      ) : (
        usage.data && <UsageSummary usage={usage.data} />
      )}

      <section aria-labelledby="events-heading" className="space-y-3">
        <h2 id="events-heading" className="sr-only">
          Events
        </h2>
        <fieldset className="flex flex-wrap gap-2">
          <legend className="sr-only">Show</legend>
          {FILTERS.map(({ value, label }) => (
            <Button
              key={label}
              size="sm"
              variant={category === value ? "secondary" : "ghost"}
              aria-pressed={category === value}
              onClick={() => setCategory(value)}
            >
              {label}
            </Button>
          ))}
        </fieldset>

        {events.isLoading ? (
          <EventsSkeleton />
        ) : events.isError ? (
          <ErrorState
            message={
              events.error instanceof ApiError ? events.error.message : "Couldn't load activity."
            }
            onRetry={() => events.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            title="Nothing recorded yet."
            description={
              category
                ? "No events in this category. Try All."
                : "Filing, settings changes, and sign-ins show up here."
            }
          />
        ) : (
          <>
            {isDesktop ? <AuditTable items={items} /> : <AuditCards items={items} />}
            {events.hasNextPage && (
              <div className="flex justify-center">
                <Button
                  variant="outline"
                  onClick={() => events.fetchNextPage()}
                  disabled={events.isFetchingNextPage}
                >
                  {events.isFetchingNextPage && <Loader2 className="animate-spin" />}
                  Load more
                </Button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function EventsSkeleton() {
  return (
    <div className="space-y-2" aria-busy="true">
      <Skeleton className="h-12 w-full" />
      <Skeleton className="h-12 w-full" />
      <Skeleton className="h-12 w-full" />
    </div>
  );
}
