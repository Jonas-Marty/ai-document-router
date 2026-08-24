import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FolderNode } from "@/services/api/types";
import { FolderPicker } from "./FolderPicker";

vi.mock("@/services/api/client", () => ({
  apiClient: { getFolderTree: vi.fn(), createFolder: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

function node(path: string): FolderNode {
  return { path, name: path.slice(1), has_children: false, children: null, file_count: 0 };
}

function renderPicker() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <FolderPicker open value="/Documents" onOpenChange={() => {}} onSelect={() => {}} />
    </QueryClientProvider>,
  );
}

describe("FolderPicker", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    // @ts-expect-error -- reset the module-scoped matchMedia stub between tests
    window.matchMedia = undefined;
  });

  it("renders as a full-screen sheet below the desktop breakpoint (SPEC 8.4)", async () => {
    vi.mocked(apiClient.getFolderTree).mockResolvedValue([node("/Documents")]);
    window.matchMedia = (query: string) =>
      ({
        matches: false,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList;

    renderPicker();

    expect(await screen.findByRole("dialog", { name: /choose folder/i })).toBeInTheDocument();
    // No arrow-key/Enter navigation on mobile (SPEC 8.4/8.5) -- the tree isn't a keyboard
    // navigation target there.
    expect(await screen.findByRole("tree")).toHaveAttribute("tabindex", "-1");
  });

  it("renders as a dialog with keyboard navigation at desktop width (SPEC 8.5)", async () => {
    vi.mocked(apiClient.getFolderTree).mockResolvedValue([node("/Documents")]);
    window.matchMedia = (query: string) =>
      ({
        matches: true,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList;

    renderPicker();

    expect(await screen.findByRole("dialog", { name: /choose folder/i })).toBeInTheDocument();
    expect(await screen.findByRole("tree")).toHaveAttribute("tabindex", "0");
  });
});
