import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { costPeriod } from "@/lib/aiCosts";
import { formatCost } from "@/lib/tokens";
import type { AnalysisCost, CostReport } from "@/services/api/types";
import { CostSection } from "./CostSection";

vi.mock("@/services/api/client", () => ({
  apiClient: { getAiCosts: vi.fn(), getAiCostAnalyses: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

const NOV_15 = new Date(2026, 10, 15, 12);
const chf = (value: number) => formatCost(value, "CHF");

const REPORT: CostReport = {
  currency: "CHF",
  priced: true,
  spent: 3,
  split: { first: 2, reanalysis: 0.5, failed: 0.25, other: 0.25 },
  days: [{ day: "2026-11-03", first: 2, reanalysis: 0.5, failed: 0.25, other: 0.25 }],
  others: [{ currency: "USD", amount: 0.38 }],
  analyses: { count: 100, priced: 98, average: 0.029, minimum: 0.004, maximum: 0.061 },
  filed_documents: 80,
  filed: { count: 80, priced: 80, average: 0.034, minimum: 0.01, maximum: 0.1 },
  configurations: [
    {
      configuration_id: "cfg",
      name: "Infomaniak · Qwen",
      is_default: true,
      analyses: 81,
      failed: 9,
      priced: 81,
      cost: 2.41,
      average: 0.031,
      tokens: 900_000,
      approvals: 40,
      approved_unchanged: 30,
    },
  ],
  expensive: [
    {
      document_id: "doc-1",
      name: "2026.06.14 UBS Kontoauszug.pdf",
      open: false,
      attempts: 4,
      failed: 2,
      page_count: 6,
      cost: 0.142,
    },
  ],
};

function analysis(overrides: Partial<AnalysisCost> = {}): AnalysisCost {
  return {
    run_id: "run-1",
    occurred_at: "2026-11-15T10:00:00+00:00",
    document_id: "doc-2",
    document_name: "scan_0412.pdf",
    document_open: true,
    kind: "first",
    configuration_name: "Infomaniak · Qwen",
    reading_cost: null,
    filing_cost: 0.0281,
    cost: 0.0281,
    currency: "CHF",
    tokens: 9_400,
    ...overrides,
  };
}

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <CostSection period={costPeriod("month", NOV_15)} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CostSection", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOV_15);
    vi.mocked(apiClient.getAiCosts).mockImplementation(async (start) =>
      start === "2026-10-01" ? { ...REPORT, spent: 2.5 } : REPORT,
    );
    vi.mocked(apiClient.getAiCostAnalyses).mockResolvedValue({
      items: [analysis()],
      total: 1,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("answers what one analysis and one filed document cost, and what was spent", async () => {
    renderSection();

    const section = await screen.findByRole("region", { name: "What an analysis costs" });
    await waitFor(() => expect(section).toHaveTextContent("vs the same days last month"));
    const figure = (label: string) => within(section).getByText(label).nextElementSibling;
    expect(figure("One analysis, on average")).toHaveTextContent(chf(0.029));
    expect(section).toHaveTextContent(`${chf(0.004)} – ${chf(0.061)}`);
    expect(section).toHaveTextContent("2 without a price");
    expect(figure("One filed document")).toHaveTextContent(chf(0.034));
    expect(figure("Spent so far")).toHaveTextContent(chf(3));
    expect(section).toHaveTextContent(`▲ ${chf(0.5)} vs the same days last month`);
    // 3 over 15 days, 30 days in November.
    expect(figure("At this pace, by month end")).toHaveTextContent(`≈ ${chf(6)}`);
    expect(apiClient.getAiCosts).toHaveBeenCalledWith("2026-10-01", "2026-10-15");
  });

  it("splits the spend by kind and keeps another currency apart", async () => {
    renderSection();

    const split = await screen.findByRole("list", { name: "Where it went" });
    expect(within(split).getByText("Failed, no proposal").parentElement).toHaveTextContent(
      `${chf(0.25)} · 8%`,
    );
    expect(screen.getByText(/Also/)).toHaveTextContent(`Also ${formatCost(0.38, "USD")}`);
  });

  it("names each configuration's cost per analysis and how often it was approved as is", async () => {
    renderSection();

    const configurations = await screen.findByRole("region", { name: "By configuration" });
    expect(configurations).toHaveTextContent(`${chf(0.031)} / analysis`);
    expect(configurations).toHaveTextContent("9 failed · 75% of 40 approved unchanged");
    expect(within(configurations).getByText("Default")).toBeInTheDocument();
    const expensive = screen.getByRole("region", { name: "Most expensive documents" });
    expect(expensive).toHaveTextContent("4 attempts · 2 failed · 6 pages");
    // Filed, so there is no review page to open.
    expect(within(expensive).queryByRole("link")).not.toBeInTheDocument();
  });

  it("lists each analysis with its kind, a link while it is open, and more on request", async () => {
    vi.mocked(apiClient.getAiCostAnalyses)
      .mockResolvedValueOnce({
        items: [analysis(), analysis({ run_id: "run-2", kind: "failed", cost: null })],
        total: 3,
      })
      .mockResolvedValueOnce({
        items: [analysis({ run_id: "run-3", kind: "reanalysis", document_open: false })],
        total: 3,
      });
    vi.useRealTimers();
    const user = userEvent.setup();
    renderSection();

    const recent = await screen.findByRole("region", { name: "Recent analyses" });
    expect(await within(recent).findAllByRole("link", { name: "scan_0412.pdf" })).toHaveLength(2);
    expect(within(recent).getByText("failed")).toBeInTheDocument();
    expect(recent).toHaveTextContent("no price");

    await user.click(within(recent).getByRole("button", { name: "Show more" }));

    expect(await within(recent).findByText("re-analysis")).toBeInTheDocument();
    expect(apiClient.getAiCostAnalyses).toHaveBeenLastCalledWith("2026-11-01", "2026-11-30", 2, 20);
    expect(within(recent).queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
  });

  it("says how to see costs when no model had a price", async () => {
    vi.mocked(apiClient.getAiCosts).mockResolvedValue({
      ...REPORT,
      priced: false,
      analyses: { count: 4, priced: 0, average: null, minimum: null, maximum: null },
    });
    renderSection();

    expect(await screen.findByText(/none on a model with a price/)).toHaveTextContent(
      "4 analyses in this period",
    );
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });
});
