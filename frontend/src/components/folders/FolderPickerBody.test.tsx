import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FolderNode } from "@/services/api/types";
import { FolderPickerBody } from "./FolderPickerBody";

vi.mock("@/services/api/client", () => ({
  apiClient: { getFolderTree: vi.fn(), createFolder: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

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

// A small fixed hierarchy shared by most tests:
//   /Documents (has children)
//     Finance (has children)
//       2026
//     Personal
//   /Scans (no children)
const TREE: Record<string, FolderNode[]> = {
  ROOT: [node("/Documents", { has_children: true }), node("/Scans")],
  "/Documents": [node("/Documents/Finance", { has_children: true }), node("/Documents/Personal")],
  "/Documents/Finance": [node("/Documents/Finance/2026")],
};

function mockTree(overrides: Partial<typeof TREE> = {}) {
  const table = { ...TREE, ...overrides };
  vi.mocked(apiClient.getFolderTree).mockImplementation(async (path?: string) => {
    const key = path ?? "ROOT";
    if (!(key in table)) throw new Error(`unexpected getFolderTree(${key})`);
    return table[key as keyof typeof table] ?? [];
  });
}

function renderBody(props: Partial<Parameters<typeof FolderPickerBody>[0]> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onSelect = vi.fn();
  const onCancel = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <FolderPickerBody
        value="/Documents"
        onSelect={onSelect}
        onCancel={onCancel}
        enableKeyboardNav={false}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onSelect, onCancel };
}

describe("FolderPickerBody", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("renders the allowed roots as the top level", async () => {
    mockTree();
    renderBody();

    expect(await screen.findByRole("treeitem", { name: /Documents/ })).toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: /Scans/ })).toBeInTheDocument();
  });

  it("does not fetch a node's children until it is expanded", async () => {
    mockTree();
    renderBody({ value: "/Documents" });

    await screen.findByRole("treeitem", { name: /Documents/ });
    // The auto-expand-to-value effect expands "/Documents" itself here since value ===
    // "/Documents" is a root with nothing to walk down to, so nothing beyond ROOT should
    // have been requested yet.
    expect(apiClient.getFolderTree).toHaveBeenCalledWith(undefined);
    expect(apiClient.getFolderTree).not.toHaveBeenCalledWith("/Documents/Finance");
  });

  it("expanding a node lazily fetches and renders its children", async () => {
    mockTree();
    const user = userEvent.setup();
    renderBody({ value: "/Scans" });

    await screen.findByRole("treeitem", { name: /Documents/ });
    expect(screen.queryByRole("treeitem", { name: /Finance/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Expand Documents/i }));

    expect(await screen.findByRole("treeitem", { name: /Finance/ })).toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: /Personal/ })).toBeInTheDocument();
  });

  it("auto-expands to and highlights the current value on open", async () => {
    mockTree();
    renderBody({ value: "/Documents/Finance/2026" });

    // Reached without ever clicking a chevron.
    const leaf = await screen.findByRole("treeitem", { name: /2026/ });
    expect(leaf).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTitle("/Documents/Finance/2026")).toBeInTheDocument();
  });

  it("clicking a row updates the footer selection without committing", async () => {
    mockTree();
    const user = userEvent.setup();
    const { onSelect } = renderBody({ value: "/Documents" });

    await user.click(await screen.findByRole("treeitem", { name: /Scans/ }));

    expect(screen.getByTitle("/Scans")).toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("commits the highlighted path only when Select is clicked", async () => {
    mockTree();
    const user = userEvent.setup();
    const { onSelect } = renderBody({ value: "/Documents" });

    await user.click(await screen.findByRole("treeitem", { name: /Scans/ }));
    await user.click(screen.getByRole("button", { name: "Select" }));

    expect(onSelect).toHaveBeenCalledWith("/Scans");
  });

  it("calls onCancel without calling onSelect", async () => {
    mockTree();
    const user = userEvent.setup();
    const { onSelect, onCancel } = renderBody({ value: "/Documents" });

    await screen.findByRole("treeitem", { name: /Documents/ });
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCancel).toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("type-to-filter narrows to nodes matching the query and their matching ancestors", async () => {
    mockTree();
    const user = userEvent.setup();
    renderBody({ value: "/Documents/Finance/2026" });

    await screen.findByRole("treeitem", { name: /2026/ });
    await user.type(screen.getByLabelText("Filter folders"), "2026");

    expect(screen.getByRole("treeitem", { name: /Documents/ })).toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: /Finance/ })).toBeInTheDocument();
    expect(screen.getByRole("treeitem", { name: /2026/ })).toBeInTheDocument();
    expect(screen.queryByRole("treeitem", { name: /Personal/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("treeitem", { name: "Scans" })).not.toBeInTheDocument();
  });

  it("creates a new folder under the selected node and immediately selects it", async () => {
    mockTree();
    vi.mocked(apiClient.createFolder).mockResolvedValue(
      node("/Documents/Archive", { has_children: false }),
    );
    const user = userEvent.setup();
    renderBody({ value: "/Documents" });

    await screen.findByRole("treeitem", { name: /Documents/ });
    await user.click(screen.getByRole("button", { name: /New folder/i }));
    await user.type(screen.getByLabelText("New folder name"), "Archive");
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(apiClient.createFolder).toHaveBeenCalledWith({
        parent_path: "/Documents",
        name: "Archive",
      });
    });
    await waitFor(() => {
      expect(screen.getByTitle("/Documents/Archive")).toBeInTheDocument();
    });
  });

  it("shows a validation error for an invalid folder name and does not call the API", async () => {
    mockTree();
    const user = userEvent.setup();
    renderBody({ value: "/Documents" });

    await screen.findByRole("treeitem", { name: /Documents/ });
    await user.click(screen.getByRole("button", { name: /New folder/i }));
    await user.type(screen.getByLabelText("New folder name"), "bad/name");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText(/can't contain \//)).toBeInTheDocument();
    expect(apiClient.createFolder).not.toHaveBeenCalled();
  });

  it("shows an error card with retry when the root list fails to load", async () => {
    vi.mocked(apiClient.getFolderTree).mockRejectedValue(new Error("boom"));
    renderBody({ value: "/Documents" });

    expect(await screen.findByText(/couldn't load folders/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("navigates with arrow keys and commits the highlighted node on Enter", async () => {
    mockTree();
    const user = userEvent.setup();
    const { onSelect } = renderBody({ value: "/Documents", enableKeyboardNav: true });

    const tree = await screen.findByRole("tree");
    tree.focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByTitle("/Scans")).toBeInTheDocument();

    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("/Scans");
  });

  it("ignores arrow keys when keyboard navigation is disabled (mobile)", async () => {
    mockTree();
    const user = userEvent.setup();
    renderBody({ value: "/Documents", enableKeyboardNav: false });

    const tree = await screen.findByRole("tree");
    tree.focus();
    await user.keyboard("{ArrowDown}");

    expect(screen.getByTitle("/Documents")).toBeInTheDocument();
  });
});
