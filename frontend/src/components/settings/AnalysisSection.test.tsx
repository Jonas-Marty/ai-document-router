import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Settings } from "@/services/api/types";
import { AnalysisSection } from "./AnalysisSection";

vi.mock("@/services/api/client", () => ({
  apiClient: { updateSettings: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { apiClient } from "@/services/api/client";

const SETTINGS: Settings = {
  allowed_root_folders: ["/Documents"],
  trash_folder_path: "/Trash",
  watch_folder_path: "/Scans/Inbox",
  filename_pattern: null,
  filename_pattern_hint: null,
  store_ocr_text: true,
  proposal_batch_size: 10,
};

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onDirtyChange = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AnalysisSection settings={SETTINGS} onDirtyChange={onDirtyChange} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onDirtyChange };
}

describe("AnalysisSection", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("points to the Lab for what each analysis sends", () => {
    renderSection();

    expect(screen.getByRole("link", { name: "Lab" })).toHaveAttribute("href", "/lab");
  });

  it("saves the batch size as a number and rejects one out of range", async () => {
    vi.mocked(apiClient.updateSettings).mockImplementation(async (body) => body);
    const user = userEvent.setup();
    renderSection();
    const field = screen.getByLabelText("Documents per batch");

    await user.clear(field);
    await user.type(field, "0");
    expect(await screen.findByText("Between 1 and 50 documents.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(apiClient.updateSettings).not.toHaveBeenCalled();

    await user.clear(field);
    await user.type(field, "20");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(apiClient.updateSettings).toHaveBeenCalledWith({
        ...SETTINGS,
        proposal_batch_size: 20,
      }),
    );
  });
});
