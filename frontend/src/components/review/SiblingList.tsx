import { ErrorState } from "@/components/shared/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import type { useFolderContext } from "@/hooks/useFolders";
import { ApiError } from "@/services/api/errors";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString();
}

/** SPEC 8.3.6: "Files already in this folder" -- the reason the app exists. Purely
 * presentational; the debounced fetch (SPEC 8.3: "re-fetches with a 300ms debounce whenever
 * the target folder changes") lives with the caller so the same query result can also drive
 * the blocking collision check next to the name field, per CLAUDE.md's single-source-of-truth
 * instinct -- two independent debounced fetches of the same endpoint would be redundant and
 * could disagree with each other. */
export function SiblingList({ query }: { query: ReturnType<typeof useFolderContext> }) {
  const { data, isLoading, isError, error, refetch } = query;

  if (isLoading) {
    return (
      <div className="space-y-1.5" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-5 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorState
        message={error instanceof ApiError ? error.message : "Couldn't load this folder."}
        onRetry={() => refetch()}
        className="p-4"
      />
    );
  }

  if (!data) {
    // The query is disabled (SPEC: folderPath empty), not merely unresolved -- distinct from
    // "loading" so the user isn't shown a spinner for a folder that hasn't been chosen yet.
    return (
      <p className="text-sm text-muted-foreground">Choose a folder to see what's already there.</p>
    );
  }

  if (data.siblings.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {data.exists ? "This folder is empty." : "This folder will be created."}
      </p>
    );
  }

  return (
    <ul className="space-y-1">
      {data.siblings.map((file) => (
        <li
          key={file.filename}
          className="flex items-baseline justify-between gap-3 font-mono text-sm"
        >
          <span className="min-w-0 truncate">{file.filename}</span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {formatDate(file.created_at)} · {formatSize(file.size_bytes)}
          </span>
        </li>
      ))}
    </ul>
  );
}
