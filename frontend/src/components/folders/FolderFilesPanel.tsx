import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import type { FolderNode } from "@/services/api/types";

/** Shows what is already in the highlighted folder, so the user can judge a destination
 * without picking it first. Files are read-only context here: only folders are selectable. */
export function FolderFilesPanel({
  selectedPath,
  node,
}: {
  selectedPath: string;
  /** Undefined while the highlighted folder's level of the tree is still loading. */
  node: FolderNode | undefined;
}) {
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
    const more = node.file_count - node.recent_files.length;
    body = (
      <ul className="space-y-0.5">
        {node.recent_files.map((name) => (
          <li key={name} className="truncate font-mono text-muted-foreground" title={name}>
            {name}
          </li>
        ))}
        {more > 0 && <li className="text-muted-foreground">+ {more} more</li>}
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
