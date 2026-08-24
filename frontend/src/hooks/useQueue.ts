import { useQuery } from "@tanstack/react-query";
import { QUEUE_LIMIT } from "@/lib/constants";
import { apiClient } from "@/services/api/client";
import { queryKeys } from "./queryKeys";

/** SPEC 8.8: refetch on a 60s interval and on window refocus (the latter is
 * TanStack Query's default). */
export function useQueue() {
  return useQuery({
    queryKey: queryKeys.queue,
    queryFn: () => apiClient.getQueue(QUEUE_LIMIT),
    refetchInterval: 60_000,
  });
}
