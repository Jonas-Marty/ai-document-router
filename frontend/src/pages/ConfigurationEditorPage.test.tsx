import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NavigationGuardProvider } from "@/hooks/useNavigationGuard";
import type { AiEndpoint, BuiltinPrompts, Configuration } from "@/services/api/types";
import ConfigurationEditorPage from "./ConfigurationEditorPage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    listConfigurations: vi.fn(),
    getBuiltinPrompts: vi.fn(),
    listAiEndpoints: vi.fn(),
    listAiModels: vi.fn(),
    createConfiguration: vi.fn(),
    updateConfiguration: vi.fn(),
    previewConfiguration: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { apiClient } from "@/services/api/client";
import { ApiError } from "@/services/api/errors";

const BUILTINS: BuiltinPrompts = {
  reading: "Read the pages.",
  filing_one_call: "Name and file it.",
  filing_fields: "Name it.",
  filing_folder: "File it.",
};

const ENDPOINT: AiEndpoint = {
  id: "ep1",
  name: "Tower",
  base_url: "http://tower:11434/v1",
  api_key_set: false,
  timeout_seconds: 120,
  context_limit: 16384,
  json_mode: "auto",
  price_input_per_million: null,
  price_output_per_million: null,
  used_by: [],
};

const SAVED: Configuration = {
  id: "c1",
  name: "Daily",
  is_default: true,
  reading_method: "text_layer",
  reading: {
    endpoint_id: null,
    endpoint_name: null,
    model_name: null,
    system_prompt: null,
    instructions: null,
    temperature: 0.1,
    max_tokens: 4096,
    thinking: "default",
  },
  filing_strategy: "one_call",
  filing: {
    endpoint_id: "ep1",
    endpoint_name: "Tower",
    model_name: "qwen3:8b",
    system_prompt: null,
    instructions: null,
    temperature: 0.1,
    max_tokens: 2048,
    thinking: "default",
  },
  folder_system_prompt: null,
  max_examples: 8,
  max_folders: 250,
  max_text_chars: 6000,
};

function renderEditor(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <NavigationGuardProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/lab/configurations/new" element={<ConfigurationEditorPage />} />
            <Route path="/lab/configurations/:id" element={<ConfigurationEditorPage />} />
          </Routes>
        </MemoryRouter>
      </NavigationGuardProvider>
    </QueryClientProvider>,
  );
}

describe("ConfigurationEditorPage", () => {
  beforeEach(() => {
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([SAVED]);
    vi.mocked(apiClient.getBuiltinPrompts).mockResolvedValue(BUILTINS);
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([ENDPOINT]);
    vi.mocked(apiClient.listAiModels).mockResolvedValue({ models: ["qwen3:8b", "gemma3"] });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("creates a configuration, sending the untouched built-in prompt as null", async () => {
    vi.mocked(apiClient.createConfiguration).mockResolvedValue({ ...SAVED, id: "c2" });
    const user = userEvent.setup();
    renderEditor("/lab/configurations/new");

    await user.type(await screen.findByLabelText("Name"), "Careful");
    await user.click(screen.getAllByRole("combobox", { name: "Connection" })[0] as HTMLElement);
    await user.click(await screen.findByRole("option", { name: "Tower" }));
    await user.click(screen.getByRole("combobox", { name: "Model" }));
    await user.click(await screen.findByRole("option", { name: "gemma3" }));
    await user.click(screen.getByRole("button", { name: "Create configuration" }));

    await waitFor(() => expect(apiClient.createConfiguration).toHaveBeenCalledTimes(1));
    const body = vi.mocked(apiClient.createConfiguration).mock.calls[0]?.[0];
    expect(body?.name).toBe("Careful");
    expect(body?.filing).toMatchObject({
      endpoint_id: "ep1",
      model_name: "gemma3",
      system_prompt: null,
      instructions: null,
      temperature: 0.1,
      max_tokens: 2048,
    });
    expect(body?.reading.endpoint_id).toBeNull();
  });

  it("refuses to save a filing step without a model", async () => {
    const user = userEvent.setup();
    renderEditor("/lab/configurations/new");

    await user.type(await screen.findByLabelText("Name"), "Careful");
    await user.click(screen.getByRole("button", { name: "Create configuration" }));

    expect(await screen.findByText("Choose a connection.")).toBeInTheDocument();
    expect(apiClient.createConfiguration).not.toHaveBeenCalled();
  });

  it("shows the built-in prompt until edited, and offers it back after", async () => {
    const user = userEvent.setup();
    renderEditor("/lab/configurations/c1");

    const prompt = await screen.findByLabelText("System prompt");
    expect(prompt).toHaveValue("Name and file it.");
    expect(screen.getByText("Built-in")).toBeInTheDocument();

    await user.type(prompt, " Carefully.");
    await user.click(screen.getByRole("button", { name: "Use the built-in prompt" }));

    expect(screen.getByLabelText("System prompt")).toHaveValue("Name and file it.");
  });

  it("swaps an unedited prompt when the strategy changes, and shows the folder prompt", async () => {
    const user = userEvent.setup();
    renderEditor("/lab/configurations/c1");

    await user.click(await screen.findByRole("combobox", { name: "Strategy" }));
    await user.click(await screen.findByRole("option", { name: "Two calls" }));

    expect(screen.getByLabelText("Fields prompt (first call)")).toHaveValue("Name it.");
    expect(screen.getByLabelText("Folder prompt (second call)")).toHaveValue("File it.");
  });

  it("asks for a reading model only once the method is a vision model", async () => {
    const user = userEvent.setup();
    renderEditor("/lab/configurations/c1");

    await screen.findByLabelText("System prompt");
    expect(screen.getAllByRole("combobox", { name: "Connection" })).toHaveLength(1);

    await user.click(screen.getByRole("combobox", { name: "Method" }));
    await user.click(await screen.findByRole("option", { name: "Vision model" }));

    expect(screen.getAllByRole("combobox", { name: "Connection" })).toHaveLength(2);
    expect(screen.getAllByLabelText("System prompt")[0]).toHaveValue("Read the pages.");
  });

  it("keeps the edits and says why when the save is refused", async () => {
    vi.mocked(apiClient.updateConfiguration).mockRejectedValue(
      new ApiError("validation_error", "There is already a configuration called 'Careful'.", 422),
    );
    const user = userEvent.setup();
    renderEditor("/lab/configurations/c1");

    const name = await screen.findByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Careful");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already a configuration");
    expect(screen.getByLabelText("Name")).toHaveValue("Careful");
  });

  it("fetches what the models are given only once asked", async () => {
    vi.mocked(apiClient.previewConfiguration).mockResolvedValue([
      {
        step: "Filing",
        model_label: "Tower · qwen3:8b",
        system_prompt: "Name and file it.",
        user_prompt: "[The document's text goes here]",
        note: "Folders are read when each document is filed.",
      },
    ]);
    const user = userEvent.setup();
    renderEditor("/lab/configurations/c1");

    await screen.findByLabelText("Name");
    expect(apiClient.previewConfiguration).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /show each call/i }));

    expect(await screen.findByText("[The document's text goes here]")).toBeInTheDocument();
    expect(apiClient.previewConfiguration).toHaveBeenCalledWith("c1");
  });
});
