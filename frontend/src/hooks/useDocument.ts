import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type { ApproveRequest } from "@/services/api/types";
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

/** Approve, skip, trash, and regenerate all change the queue (a document leaves or its
 * proposal_status changes), so each invalidates it. Approve and trash also invalidate
 * history, since both create a history entry. */
export function useApproveDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ApproveRequest }) =>
      apiClient.approveDocument(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["queue"] });
      queryClient.invalidateQueries({ queryKey: queryKeys.history() });
    },
  });
}

export function useSkipDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.skipDocument(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}

export function useTrashDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.trashDocument(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["queue"] });
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
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}
