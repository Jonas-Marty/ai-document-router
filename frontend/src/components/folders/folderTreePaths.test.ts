import { describe, expect, it } from "vitest";
import type { FolderNode } from "@/services/api/types";
import { ancestorPaths, findRootFor, nodeMatchesFilter } from "./folderTreePaths";

function node(path: string, overrides: Partial<FolderNode> = {}): FolderNode {
  return {
    path,
    name: path.split("/").filter(Boolean).at(-1) ?? "/",
    has_children: false,
    children: null,
    file_count: 0,
    ...overrides,
  };
}

describe("findRootFor", () => {
  it("finds the root a nested path lives under", () => {
    expect(findRootFor("/Documents/Finance/2026", ["/Documents", "/Scans"])).toBe("/Documents");
  });

  it("matches a multi-segment root exactly, not by a shared prefix segment", () => {
    expect(findRootFor("/Documents-Archive/x", ["/Documents"])).toBeNull();
  });

  it("returns the root itself when the path equals a root", () => {
    expect(findRootFor("/Documents", ["/Documents", "/Scans"])).toBe("/Documents");
  });

  it("returns null when nothing matches", () => {
    expect(findRootFor("/etc", ["/Documents"])).toBeNull();
  });
});

describe("ancestorPaths", () => {
  it("returns every level from the root down to (not including) the target", () => {
    expect(ancestorPaths("/Documents", "/Documents/Finance/2026/Invoices")).toEqual([
      "/Documents",
      "/Documents/Finance",
      "/Documents/Finance/2026",
    ]);
  });

  it("handles a multi-segment root correctly", () => {
    expect(ancestorPaths("/Documents/Finance", "/Documents/Finance/2026/Invoices")).toEqual([
      "/Documents/Finance",
      "/Documents/Finance/2026",
    ]);
  });

  it("returns just the root when the target is a direct child", () => {
    expect(ancestorPaths("/Documents", "/Documents/2026")).toEqual(["/Documents"]);
  });

  it("returns an empty array when the target equals the root", () => {
    expect(ancestorPaths("/Documents", "/Documents")).toEqual([]);
  });

  it("returns an empty array when the target isn't under the root", () => {
    expect(ancestorPaths("/Documents", "/Scans/Inbox")).toEqual([]);
  });

  it("tolerates a trailing slash on either side", () => {
    expect(ancestorPaths("/Documents/", "/Documents/2026/")).toEqual(["/Documents"]);
  });
});

describe("nodeMatchesFilter", () => {
  const tree: Record<string, FolderNode[]> = {
    "/Documents": [node("/Documents/Finance"), node("/Documents/Personal")],
    "/Documents/Finance": [node("/Documents/Finance/2026")],
  };
  const childrenOf = (path: string) => tree[path];

  it("matches on an empty query unconditionally", () => {
    expect(nodeMatchesFilter(node("/Documents"), "", childrenOf)).toBe(true);
  });

  it("matches case-insensitively on the node's own name", () => {
    expect(nodeMatchesFilter(node("/Documents/Finance"), "fin", childrenOf)).toBe(true);
  });

  it("matches a parent whose loaded child matches, even if the parent's own name doesn't", () => {
    expect(nodeMatchesFilter(node("/Documents"), "2026", childrenOf)).toBe(true);
  });

  it("does not match when neither the node nor any loaded descendant matches", () => {
    expect(nodeMatchesFilter(node("/Documents/Personal"), "2026", childrenOf)).toBe(false);
  });

  it("does not search into a node whose children haven't been loaded (undefined, not [])", () => {
    expect(nodeMatchesFilter(node("/Documents/Personal"), "anything-unloaded", childrenOf)).toBe(
      false,
    );
  });
});
