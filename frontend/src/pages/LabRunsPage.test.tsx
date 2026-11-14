import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Configuration } from "@/services/api/types";
import { labRun } from "@/test/labFixtures";
import LabRunsPage from "./LabRunsPage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    listLabRuns: vi.fn(),
    listTestSets: vi.fn(),
    listConfigurations: vi.fn(),
    startLabRun: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { apiClient } from "@/services/api/client";

function configuration(id: string, name: string, isDefault = false): Configuration {
  const step = {
    endpoint_id: null,
    endpoint_name: null,
    model_name: null,
    system_prompt: null,
    instructions: null,
    temperature: 0.1,
    max_tokens: 2048,
    thinking: "default" as const,
  };
  return {
    id,
    name,
    is_default: isDefault,
    reading_method: "text_layer",
    reading: step,
    filing_strategy: "two_calls",
    filing: step,
    folder_system_prompt: null,
    max_examples: 8,
    max_folders: 250,
    max_text_chars: 6000,
  };
}

function renderRuns() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/lab"]}>
        <Routes>
          <Route path="/lab" element={<LabRunsPage />} />
          <Route path="/lab/runs/:id" element={<p>Run page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LabRunsPage", () => {
  beforeEach(() => {
    vi.mocked(apiClient.listTestSets).mockResolvedValue([
      { id: "set1", name: "Bank statements", case_count: 12, created_at: "2026-11-14T09:00:00Z" },
      { id: "empty", name: "Empty", case_count: 0, created_at: "2026-11-14T09:00:00Z" },
    ]);
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([
      configuration("c1", "Daily", true),
      ...["Careful", "Fast", "Vision", "Cloud", "Mini", "Tiny"].map((name) =>
        configuration(name.toLowerCase(), name),
      ),
    ]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("lists runs with their progress and what they wait for", async () => {
    vi.mocked(apiClient.listLabRuns).mockResolvedValue([
      labRun({
        status: "running",
        finished: 3,
        total: 12,
        waiting_reason: "Waiting for Tower to file",
      }),
    ]);
    renderRuns();

    expect(await screen.findByRole("link", { name: "Bank statements" })).toHaveAttribute(
      "href",
      "/lab/runs/run1",
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "3");
    expect(screen.getByText("3 / 12")).toBeVisible();
    expect(screen.getByText("Waiting for Tower to file")).toBeVisible();
  });

  it("starts a run with the default preselected and opens it", async () => {
    vi.mocked(apiClient.listLabRuns).mockResolvedValue([]);
    vi.mocked(apiClient.startLabRun).mockResolvedValue(labRun({ id: "new" }));
    const user = userEvent.setup();
    renderRuns();

    await user.click(await screen.findByRole("button", { name: "New run" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("checkbox", { name: /daily/i })).toBeChecked();
    expect(within(dialog).getByRole("button", { name: "Start run" })).toBeEnabled();
    await user.click(within(dialog).getByRole("checkbox", { name: "Careful" }));
    await user.click(within(dialog).getByRole("button", { name: "Start run" }));

    await waitFor(() =>
      expect(apiClient.startLabRun).toHaveBeenCalledWith({
        test_set_id: "set1",
        configuration_ids: ["c1", "careful"],
      }),
    );
    expect(await screen.findByText("Run page")).toBeVisible();
  });

  it("stops at six configurations", async () => {
    vi.mocked(apiClient.listLabRuns).mockResolvedValue([]);
    const user = userEvent.setup();
    renderRuns();

    await user.click(await screen.findByRole("button", { name: "New run" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("checkbox", { name: /daily/i });
    for (const name of ["Careful", "Fast", "Vision", "Cloud", "Mini"]) {
      await user.click(within(dialog).getByRole("checkbox", { name }));
    }

    expect(within(dialog).getByRole("checkbox", { name: "Tiny" })).toBeDisabled();
    expect(within(dialog).getByRole("checkbox", { name: "Mini" })).toBeEnabled();
  });
});
