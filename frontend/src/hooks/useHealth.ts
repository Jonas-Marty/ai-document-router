import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import { queryKeys } from "./queryKeys";

const POLL_INTERVAL_MS = 15_000;

/** Drives the WebDAV outage banner (SPEC 8.10). Retries quickly and keeps polling on
 * failure, rather than backing off, so recovery -- and a backend that just came back -- is
 * noticed within one interval. */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => apiClient.getHealth(),
    refetchInterval: POLL_INTERVAL_MS,
    retry: false,
  });
}
