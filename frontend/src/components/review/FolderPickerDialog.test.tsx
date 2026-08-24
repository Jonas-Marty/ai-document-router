import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Settings } from "@/services/api/types";
import { FolderPickerDialog } from "./FolderPickerDialog";

vi.mock("@/services/api/client", () => ({
  apiClient: { getSettings: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

const settings: Settings = {
  allowed_root_folders: ["/Documents/Finance", "/Documents/Personal"],
  trash_folder_path: "/Trash",
  filename_pattern: null,
  filename_pattern_hint: null,
  ai_endpoint_url: "http://localhost:11434",
  ai_model_name: "test-model",
  ai_api_key_set: false,
};

function renderDialog(props: Partial<React.ComponentProps<typeof FolderPickerDialog>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenChange = vi.fn();
  const onSelect = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <FolderPickerDialog
        open
        onOpenChange={onOpenChange}
        value="/Documents/Finance/2026"
        onSelect={onSelect}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onOpenChange, onSelect };
}

describe("FolderPickerDialog", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("opens prefilled with the current value", async () => {
    vi.mocked(apiClient.getSettings).mockResolvedValue(settings);
    renderDialog();
    expect(await screen.findByDisplayValue("/Documents/Finance/2026")).toBeInTheDocument();
  });

  it("blocks selecting a path outside every allowed root", async () => {
    vi.mocked(apiClient.getSettings).mockResolvedValue(settings);
    const user = userEvent.setup();
    const { onSelect } = renderDialog();

    const input = await screen.findByLabelText("Folder path");
    await user.clear(input);
    await user.type(input, "/Documents/Other");

    expect(await screen.findByText(/must be inside one of:/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Select" }));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("blocks an empty path", async () => {
    vi.mocked(apiClient.getSettings).mockResolvedValue(settings);
    const user = userEvent.setup();
    renderDialog();

    const input = await screen.findByLabelText("Folder path");
    await user.clear(input);
    expect(await screen.findByText(/enter a folder path/i)).toBeInTheDocument();
  });

  it("selects a valid path and closes", async () => {
    vi.mocked(apiClient.getSettings).mockResolvedValue(settings);
    const user = userEvent.setup();
    const { onSelect, onOpenChange } = renderDialog();

    const input = await screen.findByLabelText("Folder path");
    await user.clear(input);
    await user.type(input, "/Documents/Personal/Taxes");
    await user.click(screen.getByRole("button", { name: "Select" }));

    expect(onSelect).toHaveBeenCalledWith("/Documents/Personal/Taxes");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("Cancel closes without selecting", async () => {
    vi.mocked(apiClient.getSettings).mockResolvedValue(settings);
    const user = userEvent.setup();
    const { onSelect, onOpenChange } = renderDialog();

    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(onSelect).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
