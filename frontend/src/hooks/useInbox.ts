import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { INBOX_PAGE_LIMIT } from "@/lib/constants";
import { apiClient } from "@/services/api/client";
import type { AnalyseRequest, InboxQuery } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

const IDLE_INTERVAL_MS = 60_000;
// While something is being analysed, often enough to follow it from card to card.
const ANALYSING_INTERVAL_MS = 3_000;

export type InboxFilters = Omit<InboxQuery, "offset" | "limit">;

/** SPEC 8.6: one group's cards, `Load more` by offset. Every page carries the counts, so the
 * tabs always read from the newest page fetched. */
export function useInbox(filters: InboxFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.inboxPage(filters),
    queryFn: ({ pageParam }) =>
      apiClient.getInbox({ ...filters, offset: pageParam, limit: INBOX_PAGE_LIMIT }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    // Typing in the search box must not blank the grid between keystrokes.
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const pages = query.state.data?.pages ?? [];
      const waiting = pages.some((page) => page.counts.analyse > 0);
      return waiting ? ANALYSING_INTERVAL_MS : IDLE_INTERVAL_MS;
    },
  });
}

/** Anything that moves a document between groups invalidates every Inbox page and the
 * review queue: which page a card was on is not worth tracking. */
function useInvalidateDocuments() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.inbox });
    queryClient.invalidateQueries({ queryKey: queryKeys.queue });
  };
}

export function useAnalyseDocuments() {
  const invalidate = useInvalidateDocuments();
  return useMutation({
    mutationFn: (body: AnalyseRequest) => apiClient.analyseDocuments(body),
    onSuccess: invalidate,
  });
}

export function useTrashDocuments() {
  const invalidate = useInvalidateDocuments();
  return useMutation({
    mutationFn: (ids: string[]) => apiClient.trashDocuments(ids),
    // Even a partly failed batch moved some files.
    onSettled: invalidate,
  });
}

/** SPEC 6.4b: puts a filed or trashed document back in the queue. */
export function useRevertHistoryEntry() {
  const invalidate = useInvalidateDocuments();
  return useMutation({
    mutationFn: (id: string) => apiClient.revertHistoryEntry(id),
    onSuccess: invalidate,
  });
}

/** What "Analyse with…" would cost. A query, not a mutation: it changes nothing, and the
 * dialog wants it re-asked whenever the configuration select changes. */
export function useAnalysisEstimate(ids: string[], configurationId: string | undefined) {
  return useQuery({
    queryKey: ["inbox-estimate", configurationId ?? null, ids] as const,
    queryFn: () => apiClient.estimateAnalysis(ids, configurationId as string),
    enabled: configurationId !== undefined && ids.length > 0,
  });
}

/** SPEC 8.6: connection reachability for the header, polled every minute. */
export function useEndpointStatuses() {
  return useQuery({
    queryKey: queryKeys.endpointStatuses,
    queryFn: () => apiClient.getEndpointStatuses(),
    refetchInterval: IDLE_INTERVAL_MS,
  });
}
