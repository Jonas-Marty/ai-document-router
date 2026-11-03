import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import { queryKeys } from "./queryKeys";

/** The usage page's rows for one period. The previous period stays on screen while the next
 * loads, so switching periods does not flash a skeleton. */
export function useAiUsage(start: string, end: string) {
  return useQuery({
    queryKey: queryKeys.aiUsage(start, end),
    queryFn: () => apiClient.getAiUsage(start, end),
    placeholderData: keepPreviousData,
  });
}
