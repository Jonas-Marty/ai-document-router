import { normalizeFolderPath } from "@/lib/naming";
import type { FolderNode } from "@/services/api/types";

/** Which of the allowed roots a path lives under, or null if none (shouldn't happen for a
 * value the backend accepted, but a picker opened on a stale/edited value must not crash). */
export function findRootFor(path: string, roots: string[]): string | null {
  const normalized = normalizeFolderPath(path);
  return (
    roots.find((root) => {
      const normalizedRoot = normalizeFolderPath(root);
      return normalized === normalizedRoot || normalized.startsWith(`${normalizedRoot}/`);
    }) ?? null
  );
}

/** SPEC 8.5 "auto-expands to ... the current selection": every path from `root` down to (but
 * not including) `target` that a lazy tree needs its children fetched for, in order, so the
 * selected node ends up rendered without the user clicking through each level. Returns `[]`
 * if `target` isn't under `root` at all. */
export function ancestorPaths(root: string, target: string): string[] {
  const rootNorm = normalizeFolderPath(root);
  const targetNorm = normalizeFolderPath(target);
  if (targetNorm === rootNorm || !targetNorm.startsWith(`${rootNorm}/`)) return [];

  const remainder = targetNorm.slice(rootNorm.length + 1);
  const segments = remainder.split("/").filter(Boolean);

  const result = [rootNorm];
  let acc = rootNorm;
  for (let i = 0; i < segments.length - 1; i++) {
    acc = `${acc}/${segments[i]}`;
    result.push(acc);
  }
  return result;
}

/** SPEC 8.5 "type-to-filter over loaded nodes": a node matches if its own name matches, or
 * any already-fetched descendant does. `childrenOf` looks up a node's loaded children (or
 * undefined if that level hasn't been fetched/expanded) -- this deliberately never triggers a
 * fetch, so filtering never reaches outside what's already on screen or was already loaded. */
export function nodeMatchesFilter(
  node: FolderNode,
  query: string,
  childrenOf: (path: string) => FolderNode[] | undefined,
): boolean {
  const q = query.trim().toLowerCase();
  if (q.length === 0) return true;
  if (node.name.toLowerCase().includes(q)) return true;
  const children = childrenOf(node.path);
  if (!children) return false;
  return children.some((child) => nodeMatchesFilter(child, q, childrenOf));
}
