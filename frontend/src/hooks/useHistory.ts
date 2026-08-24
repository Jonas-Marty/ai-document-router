import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { HISTORY_PAGE_LIMIT } from "@/lib/constants";
import { apiClient } from "@/services/api/client";
import { queryKeys } from "./queryKeys";

/** SPEC 8.6: newest first, "Load more" via cursor. `useInfiniteQuery` is the natural fit for
 * cursor pagination that accumulates rather than replaces -- each page's `next_cursor` feeds
 * the next fetch, and `data.pages` holds every page fetched so far for the caller to flatten. */
export function useHistory() {
  return useInfiniteQuery({
    queryKey: queryKeys.history(),
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      apiClient.getHistory(HISTORY_PAGE_LIMIT, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

/** SPEC 8.6: "On success: toast ..., invalidate history and queue" -- reverting puts the
 * document back in the review queue (SPEC 6.4), and the entry's own `revertible` flips to
 * false, so both lists are stale. Plain invalidation, not a cache patch: revert is rare
 * enough that the extra round trip isn't worth the risk of the two lists drifting apart. */
export function useRevertHistoryEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.revertHistoryEntry(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.history() });
      queryClient.invalidateQueries({ queryKey: queryKeys.queue });
    },
  });
}
