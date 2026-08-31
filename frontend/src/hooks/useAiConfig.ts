import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type { AiEndpointWrite, AiTask, AiTaskChainUpdate } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

export function useAiEndpoints() {
  return useQuery({
    queryKey: queryKeys.aiEndpoints,
    queryFn: () => apiClient.listAiEndpoints(),
  });
}

export function useAiTasks() {
  return useQuery({
    queryKey: queryKeys.aiTasks,
    queryFn: () => apiClient.listAiTasks(),
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

export function useUpdateAiTask() {
  return useInvalidatingMutation(({ task, ...body }: AiTaskChainUpdate & { task: AiTask }) =>
    apiClient.updateAiTask(task, body),
  );
}

/** Endpoints and chains reference each other -- renaming an endpoint changes how every chain
 * reads, and editing a chain changes which endpoints report as in use -- so any write to
 * either refetches both rather than trying to patch two caches in step. */
function useInvalidatingMutation<TArgs, TResult>(mutationFn: (args: TArgs) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.aiEndpoints });
      queryClient.invalidateQueries({ queryKey: queryKeys.aiTasks });
    },
  });
}
