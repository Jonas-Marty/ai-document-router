import { useQuery } from "@tanstack/react-query";
import { QUEUE_LIMIT } from "@/lib/constants";
import { apiClient } from "@/services/api/client";
import { queryKeys } from "./queryKeys";

const IDLE_INTERVAL_MS = 60_000;
// While a proposal is being made, often enough to follow its steps and show it as it lands.
const ANALYSING_INTERVAL_MS = 3_000;

/** SPEC 8.8: refetch on a 60s interval and on window refocus (the latter is TanStack
 * Query's default) -- faster while any document is still waiting for its proposal. */
export function useQueue() {
  return useQuery({
    queryKey: queryKeys.queue,
    queryFn: () => apiClient.getQueue(QUEUE_LIMIT),
    refetchInterval: (query) =>
      query.state.data?.items.some((doc) => doc.proposal_status === "pending")
        ? ANALYSING_INTERVAL_MS
        : IDLE_INTERVAL_MS,
  });
}
