import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { labResult, labRunDetail, summary } from "@/test/labFixtures";
import LabRunPage from "./LabRunPage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    getLabRun: vi.fn(),
    cancelLabRun: vi.fn(),
    deleteLabRun: vi.fn(),
    setLabVerdict: vi.fn(),
    listLabRuns: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/hooks/useBreakpoint", () => ({ useIsDesktop: () => true, useIsWide: () => true }));

import { apiClient } from "@/services/api/client";

function renderRun() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/lab/runs/run1"]}>
        <Routes>
          <Route path="/lab/runs/:id" element={<LabRunPage />} />
          <Route path="/lab" element={<p>Runs list</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LabRunPage", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("puts the best value of each column in bold", async () => {
    vi.mocked(apiClient.getLabRun).mockResolvedValue(
      labRunDetail({
        summaries: [
          summary("c1", "Daily", { fully_correct: 0.4, average_ms: 4000 }),
          summary("c2", "Careful", { fully_correct: 0.9, average_ms: 9000 }),
        ],
      }),
    );
    renderRun();

    const table = await screen.findByRole("table");
    const careful = within(table).getByRole("row", { name: /careful/i });
    const daily = within(table).getByRole("row", { name: /daily/i });
    expect(within(careful).getByText("90%")).toHaveClass("font-semibold");
    expect(within(daily).getByText("40%")).not.toHaveClass("font-semibold");
    // Faster is better: the time column favours the other configuration.
    expect(within(daily).getByText("4.0 s")).toHaveClass("font-semibold");
  });

  it("shows each document's answer and a verdict per field", async () => {
    vi.mocked(apiClient.getLabRun).mockResolvedValue(labRunDetail());
    renderRun();

    expect(await screen.findByText("scan_0001.pdf")).toBeInTheDocument();
    expect(screen.getByText("/Documents/Finance/WIR Bank/2025")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Folder: partly right. Change" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Sender: right. Change" })).toBeVisible();
  });

  it("overrules a verdict from the badge's menu", async () => {
    vi.mocked(apiClient.getLabRun).mockResolvedValue(labRunDetail());
    vi.mocked(apiClient.setLabVerdict).mockImplementation(async () => {
      // What the backend now answers, for the refetch that follows.
      vi.mocked(apiClient.getLabRun).mockResolvedValue(
        labRunDetail({}, [labResult({ overrides: { folder: 1 } })]),
      );
      return labResult({ overrides: { folder: 1 } });
    });
    const user = userEvent.setup();
    renderRun();

    await user.click(await screen.findByRole("button", { name: "Folder: partly right. Change" }));
    await user.click(await screen.findByRole("menuitem", { name: /^right/i }));

    await waitFor(() => expect(apiClient.setLabVerdict).toHaveBeenCalledWith("res1", "folder", 1));
    expect(
      await screen.findByRole("button", { name: "Folder: right, overruled. Change" }),
    ).toBeVisible();
  });

  it("goes back to the automatic verdict", async () => {
    vi.mocked(apiClient.getLabRun).mockResolvedValue(
      labRunDetail({}, [labResult({ overrides: { folder: 1 } })]),
    );
    vi.mocked(apiClient.setLabVerdict).mockResolvedValue(labResult());
    const user = userEvent.setup();
    renderRun();

    await user.click(
      await screen.findByRole("button", { name: "Folder: right, overruled. Change" }),
    );
    await user.click(await screen.findByRole("menuitem", { name: /back to automatic/i }));

    await waitFor(() =>
      expect(apiClient.setLabVerdict).toHaveBeenCalledWith("res1", "folder", null),
    );
  });

  it("offers half a point only for the folder", async () => {
    vi.mocked(apiClient.getLabRun).mockResolvedValue(labRunDetail());
    const user = userEvent.setup();
    renderRun();

    await user.click(await screen.findByRole("button", { name: "Sender: right. Change" }));

    expect(await screen.findByRole("menuitem", { name: /wrong/i })).toBeVisible();
    expect(screen.queryByRole("menuitem", { name: /partly/i })).not.toBeInTheDocument();
  });

  it("says why a result failed", async () => {
    vi.mocked(apiClient.getLabRun).mockResolvedValue(
      labRunDetail({}, [
        labResult({
          status: "failed",
          error: "The model chose '/Elsewhere'.",
          predicted_name: null,
          predicted_folder: null,
          verdicts: { date: 0, sender: 0, title: 0, folder: 0 },
        }),
      ]),
    );
    renderRun();

    expect(await screen.findByText("Failed: The model chose '/Elsewhere'.")).toBeVisible();
  });

  it("can cancel a running run and shows what it waits for", async () => {
    vi.mocked(apiClient.getLabRun).mockResolvedValue(
      labRunDetail(
        {
          status: "running",
          finished: 0,
          waiting_reason: "Waiting for Tower to file",
          finished_at: null,
        },
        [labResult({ status: "pending", verdicts: {}, predicted_name: null })],
      ),
    );
    vi.mocked(apiClient.cancelLabRun).mockResolvedValue(labRunDetail({ status: "cancelled" }));
    const user = userEvent.setup();
    renderRun();

    expect(await screen.findByText("Waiting for Tower to file")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Cancel run" }));

    await waitFor(() => expect(apiClient.cancelLabRun).toHaveBeenCalledWith("run1"));
  });

  it("says when the run is gone", async () => {
    const { ApiError } = await import("@/services/api/errors");
    vi.mocked(apiClient.getLabRun).mockRejectedValue(new ApiError("not_found", "Not found.", 404));
    renderRun();

    expect(await screen.findByText("This run no longer exists.")).toBeVisible();
  });
});
