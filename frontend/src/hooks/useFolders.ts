import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/services/api/client";
import type { CreateFolderRequest } from "@/services/api/types";
import { queryKeys } from "./queryKeys";

/** One level of the lazy tree (SPEC 8.5). `enabled` defaults to true for the root-level call
 * (`path` undefined); the picker passes `enabled: isExpanded` for every other node so a
 * collapsed folder's children are never fetched. */
export function useFolderTree(path?: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.folderTree(path),
    queryFn: () => apiClient.getFolderTree(path),
    enabled: options?.enabled ?? true,
  });
}

/** SPEC 8.3: re-fetches with a 300ms debounce whenever the target folder changes. The
 * debounce lives with the caller (it depends on user typing/selecting), not here; this hook
 * just needs `enabled` gated on a non-empty path so it doesn't fire for an empty selection. */
export function useFolderContext(path: string, filename?: string) {
  return useQuery({
    queryKey: queryKeys.folderContext(path, filename),
    queryFn: () => apiClient.getFolderContext(path, filename),
    enabled: path.length > 0,
  });
}

export function useCreateFolder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateFolderRequest) => apiClient.createFolder(body),
    onSuccess: (_node, variables) => {
      // The new folder changes its parent's children and file count.
      queryClient.invalidateQueries({ queryKey: queryKeys.folderTree(variables.parent_path) });
      queryClient.invalidateQueries({ queryKey: queryKeys.folderTree() });
    },
  });
}
