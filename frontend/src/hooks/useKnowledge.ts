import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type { ArchiveKnowledge, KnownSenderWrite } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

/** SPEC 6.3c: the name template and filing rules. */
export function useKnowledge() {
  return useQuery({ queryKey: queryKeys.knowledge, queryFn: () => apiClient.getKnowledge() });
}

export function useUpdateKnowledge() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ArchiveKnowledge) => apiClient.updateKnowledge(body),
    onSuccess: (saved) => {
      queryClient.setQueryData(queryKeys.knowledge, saved);
      // The rules are part of what each configuration's preview shows.
      queryClient.invalidateQueries({ queryKey: queryKeys.configurationPreviews });
    },
  });
}

/** Senders with their accounts, as learned from approvals. */
export function useKnownSenders() {
  return useQuery({
    queryKey: queryKeys.knownSenders,
    queryFn: () => apiClient.listKnownSenders(),
  });
}

export function useUpdateKnownSender() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: KnownSenderWrite }) =>
      apiClient.updateKnownSender(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.knownSenders }),
  });
}

export function useDeleteKnownSender() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.deleteKnownSender(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.knownSenders }),
  });
}

export function useDeleteKnownAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.deleteKnownAccount(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.knownSenders }),
  });
}
