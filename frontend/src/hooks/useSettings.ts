import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type { AiModelsRequest, SettingsUpdate } from "@/services/api/types";
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
