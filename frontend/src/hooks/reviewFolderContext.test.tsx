import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FolderContext } from "@/services/api/types";
import { useDebouncedValue } from "./useDebouncedValue";
import { useFolderContext } from "./useFolders";

// This is exactly the composition ReviewPage.tsx uses to satisfy SPEC 8.3: "re-fetches with
// a 300ms debounce whenever the target folder changes" -- the debounce lives with the
// caller, useFolderContext itself has no debounce (see useFolders.ts), so this is the seam
// that actually needs testing, not either hook alone. Uses real timers (a short, real 300ms
// wait) rather than fake timers -- mixing fake timers with TanStack Query's own internal
// scheduling made assertions hang/miss updates unpredictably; a real wait is slower but
// deterministic.

vi.mock("@/services/api/client", () => ({
  apiClient: { getFolderContext: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

function folderContextOf(path: string): FolderContext {
  return { path, exists: true, siblings: [], total_file_count: 0, filename_collision: false };
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

function useDebouncedFolderContext(path: string) {
  const debounced = useDebouncedValue(path, 300);
  return useFolderContext(debounced, "file.pdf");
}

describe("debounced folder context (SPEC 8.3 sibling list / collision refetch)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("does not fetch again for rapid intermediate changes within the debounce window", async () => {
    vi.mocked(apiClient.getFolderContext).mockImplementation((path) =>
      Promise.resolve(folderContextOf(path as string)),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result, rerender } = renderHook(({ path }) => useDebouncedFolderContext(path), {
      wrapper: wrapper(queryClient),
      initialProps: { path: "/Documents/A" },
    });

    await waitFor(() => expect(result.current.data?.path).toBe("/Documents/A"));
    expect(apiClient.getFolderContext).toHaveBeenCalledTimes(1);

    // Simulates fast typing/selection: several intermediate values before settling, all
    // within one debounce window.
    rerender({ path: "/Documents/A/B" });
    await new Promise((r) => setTimeout(r, 50));
    rerender({ path: "/Documents/A/B/C" });
    await new Promise((r) => setTimeout(r, 50));
    rerender({ path: "/Documents/Final" });

    // Only the final value's fetch should ever land -- not one per intermediate value.
    await waitFor(() => expect(result.current.data?.path).toBe("/Documents/Final"));
    expect(apiClient.getFolderContext).toHaveBeenCalledTimes(2);
    expect(apiClient.getFolderContext).toHaveBeenLastCalledWith("/Documents/Final", "file.pdf");
  });

  it("refetches (a real, separate request) once the debounce settles on a distinct folder", async () => {
    vi.mocked(apiClient.getFolderContext).mockImplementation((path) =>
      Promise.resolve(folderContextOf(path as string)),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result, rerender } = renderHook(({ path }) => useDebouncedFolderContext(path), {
      wrapper: wrapper(queryClient),
      initialProps: { path: "/Documents/Finance" },
    });

    await waitFor(() => expect(result.current.data?.path).toBe("/Documents/Finance"));

    rerender({ path: "/Documents/Personal" });
    await waitFor(() => expect(result.current.data?.path).toBe("/Documents/Personal"));

    expect(apiClient.getFolderContext).toHaveBeenCalledTimes(2);
  });
});
