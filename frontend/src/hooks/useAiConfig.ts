import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type { AiEndpointWrite, ConfigurationWrite } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

export function useAiEndpoints() {
  return useQuery({
    queryKey: queryKeys.aiEndpoints,
    queryFn: () => apiClient.listAiEndpoints(),
  });
}

export function useConfigurations() {
  return useQuery({
    queryKey: queryKeys.configurations,
    queryFn: () => apiClient.listConfigurations(),
  });
}

/** Fixed per release, so fetched once and kept. */
export function useBuiltinPrompts() {
  return useQuery({
    queryKey: queryKeys.builtinPrompts,
    queryFn: () => apiClient.getBuiltinPrompts(),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** What each call of a configuration is sent. Only fetched while someone is looking
 * (`enabled`), because building it walks the WebDAV folder tree the way a poller tick does. */
export function useConfigurationPreview(id: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.configurationPreview(id ?? ""),
    queryFn: () => apiClient.previewConfiguration(id ?? ""),
    enabled: enabled && id !== undefined,
  });
}

export function useCreateAiEndpoint() {
  return useInvalidatingMutation((body: AiEndpointWrite) => apiClient.createAiEndpoint(body));
}

export function useUpdateAiEndpoint() {
  return useInvalidatingMutation(({ id, ...body }: AiEndpointWrite & { id: string }) =>
    apiClient.updateAiEndpoint(id, body),
  );
}

export function useDeleteAiEndpoint() {
  return useInvalidatingMutation((id: string) => apiClient.deleteAiEndpoint(id));
}

export function useCreateConfiguration() {
  return useInvalidatingMutation((body: ConfigurationWrite) => apiClient.createConfiguration(body));
}

export function useUpdateConfiguration() {
  return useInvalidatingMutation(({ id, ...body }: ConfigurationWrite & { id: string }) =>
    apiClient.updateConfiguration(id, body),
  );
}

export function useDeleteConfiguration() {
  return useInvalidatingMutation((id: string) => apiClient.deleteConfiguration(id));
}

export function useDuplicateConfiguration() {
  return useInvalidatingMutation((id: string) => apiClient.duplicateConfiguration(id));
}

export function useMakeDefaultConfiguration() {
  return useInvalidatingMutation((id: string) => apiClient.makeDefaultConfiguration(id));
}

/** Connections and configurations reference each other -- renaming a connection changes how
 * every configuration reads, and editing a configuration changes which connections report as
 * in use -- so any write to either refetches both rather than patching two caches in step. */
function useInvalidatingMutation<TArgs, TResult>(mutationFn: (args: TArgs) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.aiEndpoints });
      // Also covers every preview, which is built from the configuration and its connections.
      queryClient.invalidateQueries({ queryKey: queryKeys.configurations });
    },
  });
}
