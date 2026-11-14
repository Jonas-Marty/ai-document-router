import {
  type InfiniteData,
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type { ApproveRequest, InboxPage, RegenerateChoice } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

// SPEC 8.8: often enough to follow a pending analysis step by step and show it as it lands.
const PENDING_INTERVAL_MS = 3_000;

export function useDocument(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.document(id ?? ""),
    queryFn: () => apiClient.getDocument(id as string),
    enabled: id !== undefined,
    refetchInterval: (query) =>
      query.state.data?.proposal_status === "pending" ? PENDING_INTERVAL_MS : false,
  });
}

export function prefetchDocument(queryClient: QueryClient, id: string) {
  return queryClient.prefetchQuery({
    queryKey: queryKeys.document(id),
    queryFn: () => apiClient.getDocument(id),
  });
}

export function documentContentUrl(id: string): string {
  return apiClient.getDocumentContentUrl(id);
}

/** Approve, trash and re-analyse all take a document out of Ready. SPEC 8.8 wants the row
 * gone at once, not after the next refetch, so every cached Inbox page is patched; the
 * invalidation that follows reconciles counts and order with the server. */
function dropFromInbox(queryClient: QueryClient, id: string) {
  queryClient.setQueriesData<InfiniteData<InboxPage>>({ queryKey: queryKeys.inbox }, (old) => {
    if (!old?.pages.some((page) => page.items.some((card) => card.id === id))) return old;
    return {
      ...old,
      pages: old.pages.map((page) => {
        const items = page.items.filter((card) => card.id !== id);
        return items.length === page.items.length
          ? page
          : { ...page, items, total: Math.max(0, page.total - 1) };
      }),
    };
  });
}

export function useApproveDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ApproveRequest }) =>
      apiClient.approveDocument(id, body),
    onSuccess: (response, { id }) => {
      dropFromInbox(queryClient, id);
      // A document opened by its URL lets go by its new status.
      queryClient.setQueryData(queryKeys.document(id), response.document);
      queryClient.invalidateQueries({ queryKey: queryKeys.inbox });
      // The approval may have taught a sender or an account (SPEC 6.3c).
      queryClient.invalidateQueries({ queryKey: queryKeys.knownSenders });
    },
  });
}

/** Later keeps the row in Ready, only marked, so there is nothing to patch: the refetch
 * brings the mark. The caller moves on by itself. */
export function useSkipDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.skipDocument(id),
    onSuccess: (document, id) => {
      queryClient.setQueryData(queryKeys.document(id), document);
      queryClient.invalidateQueries({ queryKey: queryKeys.inbox });
    },
  });
}

export function useTrashDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.trashDocument(id),
    onSuccess: (response, id) => {
      dropFromInbox(queryClient, id);
      queryClient.setQueryData(queryKeys.document(id), response.document);
      queryClient.invalidateQueries({ queryKey: queryKeys.inbox });
    },
  });
}

/** Retry every failed proposal at once.
 *
 * The single-document regenerate is the wrong tool after a configuration fix: the poller
 * never revisits a failed proposal, so every document that failed while the setting was
 * wrong would have to be opened and retried one at a time. */
export function useRetryFailedProposals() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.retryFailedProposals(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.inbox }),
  });
}

export function useRegenerateDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, choice }: { id: string; choice?: RegenerateChoice }) =>
      apiClient.regenerateDocument(id, choice),
    onSuccess: (document, { id }) => {
      dropFromInbox(queryClient, id);
      queryClient.setQueryData(queryKeys.document(id), document);
      queryClient.invalidateQueries({ queryKey: queryKeys.inbox });
    },
  });
}
