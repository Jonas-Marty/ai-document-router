import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiEndpoint, Configuration } from "@/services/api/types";
import LabPage from "./LabPage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    listConfigurations: vi.fn(),
    listAiEndpoints: vi.fn(),
    duplicateConfiguration: vi.fn(),
    makeDefaultConfiguration: vi.fn(),
    deleteConfiguration: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { apiClient } from "@/services/api/client";

const ENDPOINT: AiEndpoint = {
  id: "ep1",
  name: "Tower",
  base_url: "http://tower:11434/v1",
  api_key_set: false,
  timeout_seconds: 120,
  context_limit: 16384,
  json_mode: "auto",
  model_prices: [],
  used_by: ["Daily"],
};

function configuration(id: string, name: string, isDefault: boolean): Configuration {
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
    filing: { ...step, endpoint_id: "ep1", endpoint_name: "Tower", model_name: "qwen3:8b" },
    folder_system_prompt: null,
    max_examples: 8,
    max_folders: 250,
    max_text_chars: 6000,
  };
}

function renderLab() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <LabPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LabPage", () => {
  beforeEach(() => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([ENDPOINT]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("lists each configuration with its reading and filing", async () => {
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([
      configuration("c1", "Daily", true),
      configuration("c2", "Careful", false),
    ]);
    renderLab();

    const daily = (await screen.findByText("Daily")).closest("li");
    expect(daily).not.toBeNull();
    const card = within(daily as HTMLElement);
    expect(card.getByText("Default")).toBeInTheDocument();
    expect(card.getByText("Text layer")).toBeInTheDocument();
    expect(card.getByText("Tower · qwen3:8b · two calls")).toBeInTheDocument();
    // The default can be neither deleted nor made default again.
    expect(card.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(card.queryByRole("button", { name: "Make default" })).not.toBeInTheDocument();
    expect(card.getByRole("link", { name: "Edit" })).toHaveAttribute(
      "href",
      "/lab/configurations/c1",
    );
  });

  it("confirms before making another configuration the default", async () => {
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([
      configuration("c1", "Daily", true),
      configuration("c2", "Careful", false),
    ]);
    vi.mocked(apiClient.makeDefaultConfiguration).mockResolvedValue(
      configuration("c2", "Careful", true),
    );
    const user = userEvent.setup();
    renderLab();

    await user.click(await screen.findByRole("button", { name: "Make default" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(/documents waiting for an analysis switch to it/i),
    ).toBeVisible();
    expect(apiClient.makeDefaultConfiguration).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Make default" }));

    await waitFor(() => expect(apiClient.makeDefaultConfiguration).toHaveBeenCalledWith("c2"));
  });

  it("surfaces the backend's refusal to delete", async () => {
    const { ApiError } = await import("@/services/api/errors");
    const { toast } = await import("sonner");
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([
      configuration("c1", "Daily", true),
      configuration("c2", "Careful", false),
    ]);
    vi.mocked(apiClient.deleteConfiguration).mockRejectedValue(
      new ApiError("validation_error", "Not this one.", 422),
    );
    const user = userEvent.setup();
    renderLab();

    await user.click(await screen.findByRole("button", { name: "Delete" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove" }),
    );

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Not this one."));
  });

  it("says to add a connection first when there is none", async () => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([]);
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([]);
    renderLab();

    expect(await screen.findByText("Add a connection first")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /new configuration/i })).not.toBeInTheDocument();
  });

  it("invites the first configuration when there are connections", async () => {
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([]);
    renderLab();

    expect(await screen.findByText("No configurations yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New configuration" })).toBeInTheDocument();
  });
});
