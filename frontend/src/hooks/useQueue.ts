import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import { queryKeys } from "./queryKeys";

/** SPEC 8.8: refetch on a 60s interval and on window refocus (the latter is
 * TanStack Query's default). */
export function useQueue(limit?: number) {
  return useQuery({
    queryKey: queryKeys.queue(limit),
    queryFn: () => apiClient.getQueue(limit),
    refetchInterval: 60_000,
  });
}
