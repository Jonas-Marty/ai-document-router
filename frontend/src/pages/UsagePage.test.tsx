import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiUsageRow } from "@/services/api/types";
import UsagePage from "./UsagePage";

vi.mock("@/services/api/client", () => ({
  apiClient: { getAiUsage: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

function row(overrides: Partial<AiUsageRow> = {}): AiUsageRow {
  return {
    day: "2026-11-02",
    endpoint_id: "tower",
    connection: "Tower",
    connection_exists: true,
    model: "qwen3:14b",
    model_configured: true,
    feature: "filing",
    requests: 4,
    prompt_tokens: 12_000,
    completion_tokens: 400,
    cached_tokens: 0,
    without_usage: 0,
    failed: 0,
    fallback: 0,
    duration_ms: 8_000,
    timed: 4,
    cost: null,
    ...overrides,
  };
}

function setDesktop(matches: boolean): void {
  window.matchMedia = (query: string) =>
    ({
      matches,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <UsagePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("UsagePage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date(2026, 10, 3, 12) });
    vi.mocked(apiClient.getAiUsage).mockResolvedValue({
      rows: [
        row(),
        row({ model: "chandra-ocr-2", feature: "extraction", prompt_tokens: 30_000 }),
        row({
          endpoint_id: "hosted",
          connection: "Infomaniak",
          model: "gpt-4o",
          requests: 1,
          prompt_tokens: 2_000,
          completion_tokens: 100,
          fallback: 1,
        }),
        row({ requests: 2, failed: 2, prompt_tokens: 0, completion_tokens: 0 }),
      ],
      analyses: 4,
      comparisons: 1,
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    setDesktop(false);
  });

  it("asks for this month first and shows the totals", async () => {
    renderPage();

    expect(await screen.findByText("4 analyses · 1 comparisons")).toBeInTheDocument();
    expect(apiClient.getAiUsage).toHaveBeenCalledWith("2026-11-01", "2026-11-30");
    expect(screen.getAllByText("44,900").length).toBeGreaterThan(0); // 44,000 in + 900 out
    expect(screen.getByText("November 2026")).toBeInTheDocument();
  });

  it("switches period", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("November 2026");

    await user.click(screen.getByRole("button", { name: "This year" }));

    await waitFor(() =>
      expect(apiClient.getAiUsage).toHaveBeenLastCalledWith("2026-01-01", "2026-12-31"),
    );
  });

  it("opens a connection into the models it ran, and marks fallback use", async () => {
    setDesktop(true);
    const user = userEvent.setup();
    renderPage();

    // The legend has a "Tower" button too; the table's is the one that expands.
    const tower = (await screen.findAllByRole("button", { name: /^Tower/ })).find((b) =>
      b.hasAttribute("aria-expanded"),
    );
    if (!tower) throw new Error("no expandable Tower row");
    expect(screen.queryByRole("cell", { name: /chandra-ocr-2/ })).not.toBeInTheDocument();
    await user.click(tower);

    expect(tower).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("cell", { name: /chandra-ocr-2/ })).toBeInTheDocument();
    expect(screen.getAllByText("1 as fallback").length).toBeGreaterThan(0);
  });

  it("groups by feature with readable names", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Feature" }));

    const legend = screen.getAllByRole("button", { pressed: true });
    expect(legend.map((b) => b.textContent)).toEqual(expect.arrayContaining(["Reading", "Filing"]));
  });

  it("says so when nothing was used", async () => {
    vi.mocked(apiClient.getAiUsage).mockResolvedValue({ rows: [], analyses: 0, comparisons: 0 });
    renderPage();

    expect(await screen.findByText("No AI requests in this period.")).toBeInTheDocument();
    expect(
      within(screen.getByRole("group", { name: "Period" })).getAllByRole("button"),
    ).toHaveLength(4);
  });
});
