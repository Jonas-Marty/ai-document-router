import type { InboxQuery } from "@/services/api/types";

/** Central query-key factory. One source of truth so invalidation calls can't drift out of
 * sync with the keys queries actually use. */
export const queryKeys = {
  health: ["health"] as const,
  document: (id: string) => ["document", id] as const,
  folderTree: (path?: string) => ["folders", "tree", path ?? null] as const,
  folderBrowse: (path?: string) => ["folders", "browse", path ?? null] as const,
  folderContext: (path: string, filename?: string) =>
    ["folders", "context", path, filename ?? null] as const,
  folderFiles: (path: string) => ["folders", "files", path] as const,
  // Prefix of every Inbox page, for invalidating them all after anything moves a document.
  inbox: ["inbox"] as const,
  inboxPage: (params: InboxQuery) => ["inbox", params] as const,
  audit: (category?: string) => ["audit", "events", category ?? "all"] as const,
  auditUsage: ["audit", "usage"] as const,
  aiUsage: (start: string, end: string) => ["ai", "usage", start, end] as const,
  settings: ["settings"] as const,
  nextcloud: ["settings", "nextcloud"] as const,
  aiEndpoints: ["ai", "endpoints"] as const,
  endpointStatuses: ["ai", "endpoints", "status"] as const,
  configurations: ["configurations"] as const,
  builtinPrompts: ["builtin-prompts"] as const,
  // Prefix of every configuration preview, for invalidating them all at once.
  configurationPreviews: ["configurations", "preview"] as const,
  configurationPreview: (id: string) => ["configurations", "preview", id] as const,
  // Prefix of every Lab query: a run's scores and a set's cases change together.
  lab: ["lab"] as const,
  labRuns: ["lab", "runs"] as const,
  labRun: (id: string) => ["lab", "runs", id] as const,
  testSets: ["lab", "test-sets"] as const,
  testSet: (id: string) => ["lab", "test-sets", id] as const,
  knowledge: ["knowledge"] as const,
  knownSenders: ["knowledge", "senders"] as const,
  authConfig: ["auth", "config"] as const,
  currentUser: ["auth", "me"] as const,
};
