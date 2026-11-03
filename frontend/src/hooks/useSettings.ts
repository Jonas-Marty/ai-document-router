import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type { AiModelsRequest, NextcloudWrite, SettingsUpdate } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

export function useSettings() {
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: () => apiClient.getSettings(),
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsUpdate) => apiClient.updateSettings(body),
    onSuccess: (settings) => {
      queryClient.setQueryData(queryKeys.settings, settings);
      // allowed_root_folders can change what the picker and proposals see.
      queryClient.invalidateQueries({ queryKey: queryKeys.folderTree() });
      // Folders, the naming hint and the instructions are all part of what the models are sent.
      queryClient.invalidateQueries({ queryKey: queryKeys.aiContext });
    },
  });
}

/** Backs the AI section's Test button. Deliberately not a query: it must run when asked, on
 * the values currently typed into the form, and its result is not server state to cache. */
export function useListAiModels() {
  return useMutation({
    mutationFn: (body: AiModelsRequest) => apiClient.listAiModels(body),
  });
}

export function useNextcloud() {
  return useQuery({
    queryKey: queryKeys.nextcloud,
    queryFn: () => apiClient.getNextcloud(),
  });
}

export function useUpdateNextcloud() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: NextcloudWrite) => apiClient.updateNextcloud(body),
    onSuccess: (connection) => {
      queryClient.setQueryData(queryKeys.nextcloud, connection);
      // A different Nextcloud is a different set of folders, and the banner should notice now
      // rather than on its next poll.
      queryClient.invalidateQueries({ queryKey: queryKeys.health });
      queryClient.invalidateQueries({ queryKey: queryKeys.folderTree() });
      queryClient.invalidateQueries({ queryKey: queryKeys.aiContext });
    },
  });
}

/** Signs in with what is typed, not what is saved -- like useListAiModels, a mutation. */
export function useTestNextcloud() {
  return useMutation({
    mutationFn: (body: NextcloudWrite) => apiClient.testNextcloud(body),
  });
}
