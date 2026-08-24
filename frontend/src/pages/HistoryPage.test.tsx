import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { HistoryEntry, HistoryPage as HistoryPageResponse } from "@/services/api/types";
import HistoryPage from "./HistoryPage";

vi.mock("@/services/api/client", () => ({
  apiClient: { getHistory: vi.fn(), revertHistoryEntry: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { apiClient } from "@/services/api/client";

function entry(overrides: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    id: "hist-1",
    document_id: "doc-1",
    original_filename: "scan.pdf",
    final_filename: "2026.05.18 Invoice.pdf",
    final_folder_path: "/Documents/Finance",
    action: "moved",
    was_overridden: false,
    processed_at: "2026-05-18T10:00:00+00:00",
    revertible: true,
    ...overrides,
  };
}

function page(items: HistoryEntry[], nextCursor: string | null = null): HistoryPageResponse {
  return { items, next_cursor: nextCursor };
}

function renderDesktop() {
  window.matchMedia = (query: string) =>
    ({
      matches: true,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
  return renderPage();
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <HistoryPage />
        </TooltipProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
}

function mobileMatchMedia(): void {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
}

describe("HistoryPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    // Restore a working mobile-default matchMedia rather than clearing it -- tests that don't
    // call renderDesktop() rely on this still existing (setup.tsx only installs it once, at
    // file load, so leaving it undefined here would break every later test in this file that
    // doesn't set its own).
    mobileMatchMedia();
  });

  it("shows a loading skeleton before the first page resolves", () => {
    vi.mocked(apiClient.getHistory).mockReturnValue(new Promise(() => {}));
    renderPage();

    expect(screen.getByRole("heading", { name: "History" })).toBeInTheDocument();
    expect(screen.getAllByText("", { selector: "[aria-busy]" }).length).toBeGreaterThan(0);
  });

  it("shows an error card with retry on failure", async () => {
    vi.mocked(apiClient.getHistory).mockRejectedValueOnce(new Error("boom"));
    renderPage();

    expect(await screen.findByText(/couldn't load history/i)).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /try again/i });

    vi.mocked(apiClient.getHistory).mockResolvedValueOnce(page([entry()]));
    await userEvent.setup().click(retry);

    expect(await screen.findByText("2026.05.18 Invoice.pdf")).toBeInTheDocument();
  });

  it("shows the empty state when nothing has been filed", async () => {
    vi.mocked(apiClient.getHistory).mockResolvedValue(page([]));
    renderPage();

    expect(await screen.findByText(/nothing filed yet/i)).toBeInTheDocument();
  });

  it("renders mobile cards by default, with an Edited badge for overridden entries", async () => {
    vi.mocked(apiClient.getHistory).mockResolvedValue(page([entry({ was_overridden: true })]));
    renderPage();

    expect(await screen.findByText("2026.05.18 Invoice.pdf")).toBeInTheDocument();
    expect(screen.getByText("Edited")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders a table at desktop width", async () => {
    vi.mocked(apiClient.getHistory).mockResolvedValue(page([entry()]));
    renderDesktop();

    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.getByText("2026.05.18 Invoice.pdf")).toBeInTheDocument();
  });

  it("shows Load more only when a next cursor exists, and fetches the next page", async () => {
    vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
      page([entry({ id: "hist-1" })], "cursor-2"),
    );
    renderPage();

    const loadMore = await screen.findByRole("button", { name: /load more/i });

    vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
      page([entry({ id: "hist-2", final_filename: "second.pdf" })], null),
    );
    await userEvent.setup().click(loadMore);

    expect(await screen.findByText("second.pdf")).toBeInTheDocument();
    // Both pages' items stay on screen -- Load more accumulates, it doesn't replace.
    expect(screen.getByText("2026.05.18 Invoice.pdf")).toBeInTheDocument();
    expect(apiClient.getHistory).toHaveBeenLastCalledWith(20, "cursor-2");
    expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
  });

  it("confirms before reverting, naming the file and its current destination", async () => {
    vi.mocked(apiClient.getHistory).mockResolvedValue(page([entry()]));
    vi.mocked(apiClient.revertHistoryEntry).mockResolvedValue({
      history_entry: entry({ revertible: false }),
      document: {} as never,
    });
    const user = userEvent.setup();
    renderDesktop();

    await user.click(await screen.findByRole("button", { name: "Revert" }));

    const dialog = await screen.findByRole("dialog", { name: /revert this file/i });
    expect(within(dialog).getByText("2026.05.18 Invoice.pdf")).toBeInTheDocument();
    expect(within(dialog).getByText("/Documents/Finance")).toBeInTheDocument();

    expect(apiClient.revertHistoryEntry).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Revert" }));

    await waitFor(() => expect(apiClient.revertHistoryEntry).toHaveBeenCalledWith("hist-1"));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Reverted — back in the queue"));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("shows an error toast and keeps the row on a failed revert", async () => {
    vi.mocked(apiClient.getHistory).mockResolvedValue(page([entry()]));
    vi.mocked(apiClient.revertHistoryEntry).mockRejectedValue(new Error("network down"));
    const user = userEvent.setup();
    renderDesktop();

    await user.click(await screen.findByRole("button", { name: "Revert" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Revert",
      }),
    );

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.getByText("2026.05.18 Invoice.pdf")).toBeInTheDocument();
  });

  it("does not open the confirmation dialog for a non-revertible row", async () => {
    vi.mocked(apiClient.getHistory).mockResolvedValue(page([entry({ revertible: false })]));
    const user = userEvent.setup();
    renderDesktop();

    await user.click(await screen.findByRole("button", { name: "Revert" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(apiClient.revertHistoryEntry).not.toHaveBeenCalled();
  });

  it("shows the not-revertible reason in a tooltip on the disabled Revert button", async () => {
    vi.mocked(apiClient.getHistory).mockResolvedValue(page([entry({ revertible: false })]));
    const user = userEvent.setup();
    renderDesktop();

    const revertButton = await screen.findByRole("button", { name: "Revert" });
    expect(revertButton).toHaveAttribute("aria-disabled", "true");

    await user.hover(revertButton);
    expect(await screen.findByText(/already reverted, or the file has moved/i)).toBeInTheDocument();
  });
});
