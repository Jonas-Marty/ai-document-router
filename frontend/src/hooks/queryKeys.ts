/** Central query-key factory. One source of truth so invalidation calls can't drift out of
 * sync with the keys queries actually use. */
export const queryKeys = {
  health: ["health"] as const,
  queue: (limit?: number) => ["queue", limit] as const,
  document: (id: string) => ["document", id] as const,
  folderTree: (path?: string) => ["folders", "tree", path ?? null] as const,
  folderContext: (path: string, filename?: string) =>
    ["folders", "context", path, filename ?? null] as const,
  history: () => ["history"] as const,
  settings: ["settings"] as const,
};
