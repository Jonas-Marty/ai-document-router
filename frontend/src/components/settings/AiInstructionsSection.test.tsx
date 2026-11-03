import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Settings } from "@/services/api/types";
import { AiInstructionsSection } from "./AiInstructionsSection";

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
  filing_instructions: "Receipts go to /Documents/Belege.",
  extraction_instructions: null,
};

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onDirtyChange = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <AiInstructionsSection settings={SETTINGS} onDirtyChange={onDirtyChange} />
    </QueryClientProvider>,
  );
  return { onDirtyChange };
}

describe("AiInstructionsSection", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the saved instructions and a character count", () => {
    renderSection();

    expect(screen.getByLabelText("Filing instructions")).toHaveValue(
      "Receipts go to /Documents/Belege.",
    );
    expect(screen.getByLabelText("Extraction instructions")).toHaveValue("");
    expect(screen.getByText("33 / 2000")).toBeInTheDocument();
  });

  it("saves trimmed text, and blank as null", async () => {
    vi.mocked(apiClient.updateSettings).mockImplementation(async (body) => body);
    const user = userEvent.setup();
    const { onDirtyChange } = renderSection();

    await user.clear(screen.getByLabelText("Filing instructions"));
    await user.type(screen.getByLabelText("Extraction instructions"), "  Keep the umlauts.  ");
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(apiClient.updateSettings).toHaveBeenCalledWith({
        ...SETTINGS,
        filing_instructions: null,
        extraction_instructions: "Keep the umlauts.",
      }),
    );
  });

  it("flags text over the limit and refuses to send it", async () => {
    const user = userEvent.setup();
    renderSection();

    // fireEvent rather than typing 2001 characters one keystroke at a time.
    fireEvent.change(screen.getByLabelText("Extraction instructions"), {
      target: { value: "x".repeat(2001) },
    });

    expect(await screen.findByText("Keep it to 2000 characters or fewer.")).toBeInTheDocument();
    expect(screen.getByLabelText("Extraction instructions")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(apiClient.updateSettings).not.toHaveBeenCalled();
  });
});
