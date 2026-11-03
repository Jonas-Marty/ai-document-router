import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { AUDIT_PAGE_LIMIT, AUDIT_USAGE_DAYS } from "@/lib/constants";
import { apiClient } from "@/services/api/client";
import type { AuditCategory } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

/** SPEC 8.11: newest first, "Load more" via cursor, the same shape as useHistory. Each
 * category is its own cache entry, so switching the filter back shows what was already
 * loaded instead of starting over. */
export function useAuditEvents(category: AuditCategory | undefined) {
  return useInfiniteQuery({
    queryKey: queryKeys.audit(category),
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      apiClient.getAudit(AUDIT_PAGE_LIMIT, pageParam, category),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

export function useAuditUsage() {
  return useQuery({
    queryKey: queryKeys.auditUsage,
    queryFn: () => apiClient.getAuditUsage(AUDIT_USAGE_DAYS),
  });
}
