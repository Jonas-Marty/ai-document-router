import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type { ApproveRequest, QueueResponse } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

export function useDocument(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.document(id ?? ""),
    queryFn: () => apiClient.getDocument(id as string),
    enabled: id !== undefined,
  });
}

export function documentContentUrl(id: string): string {
  return apiClient.getDocumentContentUrl(id);
}

/** Approve and trash both remove their document from the queue for good. SPEC 8.8 wants that
 * reflected immediately ("drop from the cached queue, advance"), not after the next 60s
 * refetch lands -- so this patches the cache directly rather than only invalidating.
 * `total_pending` only drops if the document was actually in the cached list (it always was,
 * since only queued documents can be approved/trashed, but a stale cache is still a cache). */
function removeFromQueueCache(queryClient: QueryClient, id: string) {
  queryClient.setQueryData<QueueResponse>(queryKeys.queue, (old) => {
    if (!old) return old;
    if (!old.items.some((doc) => doc.id === id)) return old;
    return {
      items: old.items.filter((doc) => doc.id !== id),
      total_pending: Math.max(0, old.total_pending - 1),
    };
  });
}

/** Approve, skip, trash, and regenerate all change the queue, so each invalidates it as a
 * consistency backstop even where a cache patch already gave the UI its instant update --
 * once the background refetch lands it reconciles with the server's actual ordering. Approve
 * and trash also invalidate history, since both create a history entry. */
export function useApproveDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ApproveRequest }) =>
      apiClient.approveDocument(id, body),
    onSuccess: (_response, { id }) => {
      removeFromQueueCache(queryClient, id);
      queryClient.invalidateQueries({ queryKey: queryKeys.queue });
      queryClient.invalidateQueries({ queryKey: queryKeys.history() });
    },
  });
}

/** No cache patch here, unlike approve/trash: a skipped document stays in the queue, just
 * reordered to the back (SPEC 5's skip_count ordering) -- reproducing that ordering
 * client-side would just be a second, divergence-prone copy of server logic. The caller
 * (ReviewPage) advances its own current-document pointer directly in this mutation's
 * per-call onSuccess; this hook-level one only keeps the cache eventually consistent. */
export function useSkipDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.skipDocument(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.queue });
    },
  });
}

export function useTrashDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.trashDocument(id),
    onSuccess: (_response, id) => {
      removeFromQueueCache(queryClient, id);
      queryClient.invalidateQueries({ queryKey: queryKeys.queue });
      queryClient.invalidateQueries({ queryKey: queryKeys.history() });
    },
  });
}

export function useRegenerateDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.regenerateDocument(id),
    onSuccess: (_document, id) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.document(id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.queue });
    },
  });
}
