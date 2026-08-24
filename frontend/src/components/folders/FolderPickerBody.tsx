import { useQueries } from "@tanstack/react-query";
import { FolderPlus, Search } from "lucide-react";
import type { KeyboardEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { queryKeys } from "@/hooks/queryKeys";
import { useCreateFolder, useFolderTree } from "@/hooks/useFolders";
import { validateFolderName } from "@/lib/naming";
import { apiClient } from "@/services/api/client";
import { ApiError } from "@/services/api/errors";
import type { FolderNode } from "@/services/api/types";
import type { FolderChildrenState } from "./FolderTreeNode";
import { FolderTreeNode } from "./FolderTreeNode";
import { ancestorPaths, findRootFor, nodeMatchesFilter } from "./folderTreePaths";

export interface FolderPickerBodyProps {
  /** The folder currently set on the review form -- the picker auto-expands and highlights
   * down to this on open (SPEC 8.5). */
  value: string;
  /** Commits the highlighted/selected path. Does not close the picker itself -- the wrapper
   * (Dialog on desktop, Sheet on mobile) composes that. */
  onSelect: (path: string) => void;
  onCancel: () => void;
  /** SPEC 8.5: arrow-key navigation and Enter to select is a desktop-only affordance. */
  enableKeyboardNav: boolean;
}

interface VisibleEntry {
  path: string;
  node: FolderNode;
  parent: string | null;
}

function collectVisible(
  nodes: FolderNode[],
  parent: string | null,
  expandedPaths: Set<string>,
  childrenByPath: Map<string, FolderChildrenState>,
  filter: string,
  out: VisibleEntry[],
): void {
  for (const node of nodes) {
    if (!nodeMatchesFilter(node, filter, (p) => childrenByPath.get(p)?.data)) continue;
    out.push({ path: node.path, node, parent });
    if (expandedPaths.has(node.path)) {
      const kids = childrenByPath.get(node.path)?.data;
      if (kids) collectVisible(kids, node.path, expandedPaths, childrenByPath, filter, out);
    }
  }
}

/** SPEC 8.5's folder picker body, shared by the desktop dialog and the mobile sheet. Owns
 * the tree's fetching (one `useQueries` call over every expanded path -- lazy, since a
 * collapsed node is simply not in that set), the draft selection, type-to-filter, and
 * create-folder. The wrapper only supplies `value`/`onSelect`/`onCancel` and whether keyboard
 * navigation applies. */
export function FolderPickerBody({
  value,
  onSelect,
  onCancel,
  enableKeyboardNav,
}: FolderPickerBodyProps) {
  const rootsQuery = useFolderTree();
  const createFolder = useCreateFolder();

  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set());
  const [selectedPath, setSelectedPath] = useState(value);
  const [filter, setFilter] = useState("");
  const [creatingUnder, setCreatingUnder] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const didAutoExpand = useRef(false);

  // Auto-expand to `value` exactly once, as soon as the root list (which is where the
  // allowed-roots strings actually come from -- not settings, so this can't drift from what
  // the tree itself considers a root) has loaded. This has to be an effect, not a render-phase
  // update: StrictMode double-invokes the render body in dev, and a ref mutated directly
  // during render flips `didAutoExpand.current` true on the throwaway first pass, so the
  // second (kept) pass silently skips the setState -- an effect's ref guard doesn't have that
  // problem, since React only runs the *committed* effect (StrictMode's mount-cleanup-remount
  // cycle is idempotent here because the ref state carries through it correctly). Depending on
  // `rootsQuery.data` (not an empty array) is what lets this fire once the data actually
  // arrives rather than only on mount; the ref guard is what stops it from re-firing and
  // silently collapsing whatever the user expanded by hand when folder creation invalidates
  // the root query later.
  useEffect(() => {
    if (didAutoExpand.current || !rootsQuery.data) return;
    didAutoExpand.current = true;
    const roots = rootsQuery.data.map((n) => n.path);
    const root = findRootFor(value, roots);
    if (root) setExpandedPaths(new Set(ancestorPaths(root, value)));
  }, [rootsQuery.data, value]);

  const expandedList = [...expandedPaths];
  const childrenResults = useQueries({
    queries: expandedList.map((path) => ({
      queryKey: queryKeys.folderTree(path),
      queryFn: () => apiClient.getFolderTree(path),
    })),
  });
  const childrenByPath = new Map<string, FolderChildrenState>();
  expandedList.forEach((path, i) => {
    const r = childrenResults[i];
    if (!r) return;
    childrenByPath.set(path, {
      data: r.data,
      isLoading: r.isLoading,
      isError: r.isError,
      errorMessage: r.error instanceof ApiError ? r.error.message : "Couldn't load subfolders.",
      refetch: () => {
        r.refetch();
      },
    });
  });

  function handleToggle(path: string) {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function registerRef(path: string, el: HTMLDivElement | null) {
    if (el) rowRefs.current.set(path, el);
    else rowRefs.current.delete(path);
  }

  function highlight(path: string) {
    setSelectedPath(path);
    rowRefs.current.get(path)?.scrollIntoView({ block: "nearest" });
  }

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (!enableKeyboardNav) return;
    const visible: VisibleEntry[] = [];
    if (rootsQuery.data) {
      collectVisible(rootsQuery.data, null, expandedPaths, childrenByPath, filter, visible);
    }
    const idx = visible.findIndex((v) => v.path === selectedPath);

    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = visible[Math.min(idx + 1, visible.length - 1)];
      if (next) highlight(next.path);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = visible[Math.max(idx - 1, 0)];
      if (prev) highlight(prev.path);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      const current = visible[idx];
      if (current?.node.has_children) {
        if (!expandedPaths.has(current.path)) {
          handleToggle(current.path);
        } else {
          const kids = childrenByPath.get(current.path)?.data;
          if (kids?.[0]) highlight(kids[0].path);
        }
      }
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      const current = visible[idx];
      if (current && expandedPaths.has(current.path)) {
        handleToggle(current.path);
      } else if (current?.parent) {
        highlight(current.parent);
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      onSelect(selectedPath);
    }
  }

  function openNewFolder() {
    setCreatingUnder(selectedPath);
    setNewFolderName("");
    setCreateError(null);
  }

  function handleCreateFolder() {
    if (!creatingUnder) return;
    const trimmed = newFolderName.trim();
    const validationError = validateFolderName(trimmed);
    if (validationError) {
      setCreateError(validationError);
      return;
    }
    createFolder.mutate(
      { parent_path: creatingUnder, name: trimmed },
      {
        onSuccess: (created) => {
          setExpandedPaths((prev) => new Set(prev).add(creatingUnder));
          setSelectedPath(created.path);
          setCreatingUnder(null);
          setNewFolderName("");
          setCreateError(null);
        },
        onError: (error) => {
          setCreateError(error instanceof ApiError ? error.message : "Couldn't create the folder.");
        },
      },
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border p-2">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter folders…"
            aria-label="Filter folders"
            className="h-8 pl-7 text-sm"
          />
        </div>
        <Button variant="outline" size="sm" onClick={openNewFolder} disabled={!selectedPath}>
          <FolderPlus className="size-4" aria-hidden="true" />
          New folder
        </Button>
      </div>

      {rootsQuery.isLoading ? (
        <div className="space-y-1 p-2" aria-busy="true">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : rootsQuery.isError ? (
        <ErrorState
          className="border-0"
          message={
            rootsQuery.error instanceof ApiError
              ? rootsQuery.error.message
              : "Couldn't load folders."
          }
          onRetry={() => rootsQuery.refetch()}
        />
      ) : rootsQuery.data && rootsQuery.data.length === 0 ? (
        <EmptyState
          title="No allowed folders configured."
          description="Set at least one in Settings first."
        />
      ) : (
        <div
          ref={containerRef}
          role="tree"
          aria-label="Folders"
          tabIndex={enableKeyboardNav ? 0 : -1}
          onKeyDown={handleKeyDown}
          className="flex-1 overflow-y-auto p-1 outline-none"
        >
          {rootsQuery.data?.map((root) => (
            <FolderTreeNode
              key={root.path}
              node={root}
              depth={0}
              selectedPath={selectedPath}
              expandedPaths={expandedPaths}
              childrenByPath={childrenByPath}
              filter={filter}
              onSelect={highlight}
              onToggle={handleToggle}
              registerRef={registerRef}
            />
          ))}
        </div>
      )}

      {creatingUnder && (
        <div className="space-y-1.5 border-t border-border p-2">
          <p className="truncate text-xs text-muted-foreground">New folder in {creatingUnder}</p>
          <div className="flex items-center gap-2">
            <Input
              autoFocus
              value={newFolderName}
              onChange={(e) => {
                setNewFolderName(e.target.value);
                setCreateError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleCreateFolder();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  setCreatingUnder(null);
                }
              }}
              aria-label="New folder name"
              aria-invalid={!!createError}
              className="h-8 text-sm"
            />
            <Button size="sm" onClick={handleCreateFolder} disabled={createFolder.isPending}>
              Create
            </Button>
            <Button variant="outline" size="sm" onClick={() => setCreatingUnder(null)}>
              Cancel
            </Button>
          </div>
          {createError && <p className="text-sm text-destructive">{createError}</p>}
        </div>
      )}

      <div className="flex items-center justify-between gap-2 border-t border-border p-3">
        <p
          className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground"
          title={selectedPath}
        >
          {selectedPath || "No folder selected"}
        </p>
        <div className="flex shrink-0 gap-2">
          <Button variant="outline" size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button size="sm" disabled={!selectedPath} onClick={() => onSelect(selectedPath)}>
            Select
          </Button>
        </div>
      </div>
    </div>
  );
}
