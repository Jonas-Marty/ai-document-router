import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Document, QueueResponse } from "@/services/api/types";
import { queryKeys } from "./queryKeys";
import { useReviewQueue } from "./useReviewQueue";

vi.mock("@/services/api/client", () => ({
  apiClient: { getQueue: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

function doc(id: string): Document {
  return {
    id,
    original_filename: `${id}.pdf`,
    extension: ".pdf",
    mime_type: "application/pdf",
    file_size_bytes: 100,
    page_count: 1,
    scanned_at: "2026-01-01T00:00:00Z",
    status: "pending",
    skip_count: 0,
    proposal_status: "ready",
    proposal: null,
    proposal_error: null,
    ocr_status: "not_needed",
    ocr_error: null,
  };
}

function queueOf(...ids: string[]): QueueResponse {
  return { items: ids.map(doc), total_pending: ids.length };
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("useReviewQueue", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("starts on the first document", async () => {
    vi.mocked(apiClient.getQueue).mockResolvedValue(queueOf("a", "b", "c"));
    const { result } = renderHook(() => useReviewQueue(), { wrapper: wrapper(queryClient) });

    await waitFor(() => expect(result.current.currentDocument?.id).toBe("a"));
  });

  it("jumps to a document picked out of the queue overview", async () => {
    vi.mocked(apiClient.getQueue).mockResolvedValue(queueOf("a", "b", "c"));
    const { result } = renderHook(() => useReviewQueue(), { wrapper: wrapper(queryClient) });
    await waitFor(() => expect(result.current.currentDocument?.id).toBe("a"));

    act(() => result.current.selectDocument("c"));

    expect(result.current.currentDocument?.id).toBe("c");
  });

  it("ignores a pick for a document that has left the queue", async () => {
    vi.mocked(apiClient.getQueue).mockResolvedValue(queueOf("a", "b"));
    const { result } = renderHook(() => useReviewQueue(), { wrapper: wrapper(queryClient) });
    await waitFor(() => expect(result.current.currentDocument?.id).toBe("a"));

    // The 60s refetch can remove one between the list rendering and the click landing;
    // following it would blank the review pane instead of saying anything useful.
    act(() => result.current.selectDocument("gone"));

    expect(result.current.currentDocument?.id).toBe("a");
  });

  it("reports the whole backlog, not just the page /queue returned", async () => {
    vi.mocked(apiClient.getQueue).mockResolvedValue({
      items: [doc("a"), doc("b")],
      total_pending: 37,
    });
    const { result } = renderHook(() => useReviewQueue(), { wrapper: wrapper(queryClient) });

    await waitFor(() => expect(result.current.totalPending).toBe(37));
    expect(result.current.items).toHaveLength(2);
  });

  it("advances to the next document once the current one is removed from the cache (approve/trash)", async () => {
    vi.mocked(apiClient.getQueue).mockResolvedValue(queueOf("a", "b", "c"));
    const { result } = renderHook(() => useReviewQueue(), { wrapper: wrapper(queryClient) });
    await waitFor(() => expect(result.current.currentDocument?.id).toBe("a"));

    // Simulates exactly what useApproveDocument/useTrashDocument's onSuccess does.
    queryClient.setQueryData<QueueResponse>(queryKeys.queue, (old) =>
      old
        ? { items: old.items.filter((d) => d.id !== "a"), total_pending: old.total_pending - 1 }
        : old,
    );

    await waitFor(() => expect(result.current.currentDocument?.id).toBe("b"));
  });

  it("does not move when a mutation fails -- nothing calls setQueryData or advancePast", async () => {
    vi.mocked(apiClient.getQueue).mockResolvedValue(queueOf("a", "b", "c"));
    const { result } = renderHook(() => useReviewQueue(), { wrapper: wrapper(queryClient) });
    await waitFor(() => expect(result.current.currentDocument?.id).toBe("a"));

    // A failed approve calls neither removeFromQueueCache nor advancePast (see useDocument.ts
    // and ReviewPage) -- simulated here by simply doing nothing and asserting no drift.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(result.current.currentDocument?.id).toBe("a");
  });

  it("advancePast moves off a document that is skipped but stays in the queue", async () => {
    vi.mocked(apiClient.getQueue).mockResolvedValue(queueOf("a", "b", "c"));
    const { result } = renderHook(() => useReviewQueue(), { wrapper: wrapper(queryClient) });
    await waitFor(() => expect(result.current.currentDocument?.id).toBe("a"));

    result.current.advancePast("a");

    await waitFor(() => expect(result.current.currentDocument?.id).toBe("b"));
  });

  it("advancePast is a no-op if the named id is not the current one", async () => {
    vi.mocked(apiClient.getQueue).mockResolvedValue(queueOf("a", "b", "c"));
    const { result } = renderHook(() => useReviewQueue(), { wrapper: wrapper(queryClient) });
    await waitFor(() => expect(result.current.currentDocument?.id).toBe("a"));

    result.current.advancePast("b"); // stale call, e.g. a slow response for a document that
    // is no longer current -- must not steal the pointer.

    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(result.current.currentDocument?.id).toBe("a");
  });

  it("falls back to null when the queue empties entirely", async () => {
    vi.mocked(apiClient.getQueue).mockResolvedValue(queueOf("a"));
    const { result } = renderHook(() => useReviewQueue(), { wrapper: wrapper(queryClient) });
    await waitFor(() => expect(result.current.currentDocument?.id).toBe("a"));

    queryClient.setQueryData<QueueResponse>(queryKeys.queue, { items: [], total_pending: 0 });

    await waitFor(() => expect(result.current.currentDocument).toBeUndefined());
  });
});
