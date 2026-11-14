import { type ReactNode, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { useFolderFiles } from "@/hooks/useFolders";
import type { FolderNode } from "@/services/api/types";

/** Shows what is already in the highlighted folder, so the user can judge a destination
 * without picking it first. Files are read-only context here: only folders are selectable. */
export function FolderFilesPanel({
  selectedPath,
  node,
  canLoadAll = false,
}: {
  selectedPath: string;
  /** Undefined while the highlighted folder's level of the tree is still loading. */
  node: FolderNode | undefined;
  /** Only inside the allowed folders: the full listing is checked against them. */
  canLoadAll?: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const all = useFolderFiles(selectedPath, showAll);
  let body: ReactNode;
  if (!selectedPath) {
    body = <p className="text-muted-foreground">Select a folder to see its files.</p>;
  } else if (!node) {
    body = (
      <div className="space-y-1" aria-busy="true">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    );
  } else if (node.file_count === 0) {
    body = <p className="text-muted-foreground">No files in this folder.</p>;
  } else {
    const names = all.data ? all.data.siblings.map((file) => file.filename) : node.recent_files;
    const more = (all.data?.total_file_count ?? node.file_count) - names.length;
    body = (
      <ul className="space-y-0.5">
        {names.map((name) => (
          <li key={name} className="truncate font-mono text-muted-foreground" title={name}>
            {name}
          </li>
        ))}
        {more > 0 && (
          <li className="text-muted-foreground">
            {canLoadAll && !showAll ? (
              <button
                type="button"
                className="rounded-sm underline underline-offset-4 hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
                onClick={() => setShowAll(true)}
              >
                + {more} more
              </button>
            ) : all.isFetching ? (
              "Loading…"
            ) : all.isError ? (
              "Couldn't load the other files."
            ) : (
              `+ ${more} more`
            )}
          </li>
        )}
      </ul>
    );
  }

  return (
    <section
      aria-label="Files in folder"
      className="flex max-h-[40%] min-h-24 shrink-0 flex-col border-t border-border"
    >
      {selectedPath && (
        <p className="truncate px-3 pt-2 text-xs font-medium">
          Files in {selectedPath}
          {node && <span className="text-muted-foreground"> · {node.file_count}</span>}
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2 text-xs">{body}</div>
    </section>
  );
}
