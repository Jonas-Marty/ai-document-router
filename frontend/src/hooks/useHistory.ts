import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import { queryKeys } from "./queryKeys";

/** SPEC 8.6: "Load more" via cursor. An infinite query accumulates pages naturally, so
 * the History page just calls fetchNextPage() from a button. */
export function useHistory(limit?: number) {
  return useInfiniteQuery({
    queryKey: queryKeys.history(),
    queryFn: ({ pageParam }) => apiClient.getHistory(limit, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

export function useRevertHistoryEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.revertHistoryEntry(id),
    onSuccess: () => {
      // SPEC 8.6: on success, invalidate history and queue -- the document is back in it.
      queryClient.invalidateQueries({ queryKey: queryKeys.history() });
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}
