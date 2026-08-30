import { QUEUE_LIMIT } from "@/lib/constants";

/** Central query-key factory. One source of truth so invalidation calls can't drift out of
 * sync with the keys queries actually use. `queue` takes no argument -- the app has exactly
 * one queue view, and a parameterised key previously let a caller silently write to a cache
 * entry nobody reads (e.g. `queue(20)` vs. `queue(undefined)`). */
export const queryKeys = {
  health: ["health"] as const,
  queue: ["queue", QUEUE_LIMIT] as const,
  document: (id: string) => ["document", id] as const,
  folderTree: (path?: string) => ["folders", "tree", path ?? null] as const,
  folderContext: (path: string, filename?: string) =>
    ["folders", "context", path, filename ?? null] as const,
  history: () => ["history"] as const,
  settings: ["settings"] as const,
  authConfig: ["auth", "config"] as const,
  currentUser: ["auth", "me"] as const,
};
