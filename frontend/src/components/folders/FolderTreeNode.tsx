import { ChevronDown, ChevronRight, Folder } from "lucide-react";
import { cn } from "@/lib/utils";
import type { FolderNode } from "@/services/api/types";
import { nodeMatchesFilter } from "./folderTreePaths";

export interface FolderChildrenState {
  data?: FolderNode[];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  refetch: () => void;
}

export interface FolderTreeNodeProps {
  node: FolderNode;
  depth: number;
  selectedPath: string;
  expandedPaths: Set<string>;
  childrenByPath: Map<string, FolderChildrenState>;
  filter: string;
  onSelect: (path: string) => void;
  onToggle: (path: string) => void;
  registerRef: (path: string, el: HTMLDivElement | null) => void;
}

const INDENT_PX = 20;
const BASE_PADDING_PX = 8;

/** One row of the SPEC 8.5 lazy tree, plus (recursively) its expanded children. Fetching
 * itself lives one level up in `FolderPickerBody` (a single `useQueries` over every expanded
 * path) so this stays a plain, filter-aware renderer -- it never triggers a fetch itself. */
export function FolderTreeNode({
  node,
  depth,
  selectedPath,
  expandedPaths,
  childrenByPath,
  filter,
  onSelect,
  onToggle,
  registerRef,
}: FolderTreeNodeProps) {
  const childrenOf = (path: string) => childrenByPath.get(path)?.data;
  if (!nodeMatchesFilter(node, filter, childrenOf)) return null;

  const isExpanded = expandedPaths.has(node.path);
  const isSelected = node.path === selectedPath;
  const childState = childrenByPath.get(node.path);
  const indent = depth * INDENT_PX + BASE_PADDING_PX;

  return (
    <div>
      <div
        ref={(el) => registerRef(node.path, el)}
        role="treeitem"
        aria-selected={isSelected}
        aria-expanded={node.has_children ? isExpanded : undefined}
        data-path={node.path}
        // Real keyboard traversal is handled by the ancestor `role="tree"` container's own
        // onKeyDown (arrow keys move a single roving `selectedPath`, SPEC 8.5) -- rows are
        // intentionally out of tab order (tabIndex=-1) rather than each independently
        // focusable, so Enter/Space here just mirror the same commit a click does.
        tabIndex={-1}
        onClick={() => onSelect(node.path)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(node.path);
          }
        }}
        className={cn(
          "flex min-h-9 cursor-pointer items-center gap-1.5 rounded-md pr-2 text-sm",
          isSelected ? "bg-accent text-accent-foreground" : "hover:bg-accent/50",
        )}
        style={{ paddingLeft: `${indent}px` }}
      >
        {node.has_children ? (
          <button
            type="button"
            className="shrink-0 rounded p-0.5 hover:bg-background"
            onClick={(e) => {
              e.stopPropagation();
              onToggle(node.path);
            }}
            aria-label={isExpanded ? `Collapse ${node.name}` : `Expand ${node.name}`}
          >
            {isExpanded ? (
              <ChevronDown className="size-3.5" aria-hidden="true" />
            ) : (
              <ChevronRight className="size-3.5" aria-hidden="true" />
            )}
          </button>
        ) : (
          <span className="size-3.5 shrink-0" aria-hidden="true" />
        )}
        <Folder className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
        {node.file_count > 0 && (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {node.file_count}
          </span>
        )}
      </div>
      {isExpanded && (
        // biome-ignore lint/a11y/useSemanticElements: role="group" is the ARIA treeitem-children pattern, not a form grouping -- <fieldset> is wrong here.
        <div role="group">
          {childState?.isLoading && (
            <p
              style={{ paddingLeft: `${indent + INDENT_PX}px` }}
              className="py-1 text-xs text-muted-foreground"
            >
              Loading…
            </p>
          )}
          {childState?.isError && (
            <p
              style={{ paddingLeft: `${indent + INDENT_PX}px` }}
              className="py-1 text-xs text-destructive"
            >
              {childState.errorMessage}{" "}
              <button
                type="button"
                className="underline underline-offset-2"
                onClick={() => childState.refetch()}
              >
                Retry
              </button>
            </p>
          )}
          {childState?.data?.length === 0 && (
            <p
              style={{ paddingLeft: `${indent + INDENT_PX}px` }}
              className="py-1 text-xs text-muted-foreground"
            >
              No subfolders
            </p>
          )}
          {childState?.data?.map((child) => (
            <FolderTreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              expandedPaths={expandedPaths}
              childrenByPath={childrenByPath}
              filter={filter}
              onSelect={onSelect}
              onToggle={onToggle}
              registerRef={registerRef}
            />
          ))}
        </div>
      )}
    </div>
  );
}
