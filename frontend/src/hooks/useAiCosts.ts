import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import { queryKeys } from "./queryKeys";

export const COST_ANALYSES_PAGE = 20;

/** SPEC 8.11: what an analysis cost over a period. The previous period stays on screen while
 * the next loads, so switching periods does not flash a skeleton. */
export function useAiCosts(start: string, end: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.aiCosts(start, end),
    queryFn: () => apiClient.getAiCosts(start, end),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** The period's analyses, newest first, a page at a time behind "Show more". */
export function useAiCostAnalyses(start: string, end: string) {
  return useInfiniteQuery({
    queryKey: queryKeys.aiCostAnalyses(start, end),
    queryFn: ({ pageParam }: { pageParam: number }) =>
      apiClient.getAiCostAnalyses(start, end, pageParam, COST_ANALYSES_PAGE),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    placeholderData: keepPreviousData,
  });
}
