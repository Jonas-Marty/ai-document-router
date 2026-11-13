import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/services/api/errors";
import type { AiEndpoint } from "@/services/api/types";
import { AiEndpointsSection } from "./AiEndpointsSection";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    listAiEndpoints: vi.fn(),
    createAiEndpoint: vi.fn(),
    updateAiEndpoint: vi.fn(),
    deleteAiEndpoint: vi.fn(),
    listAiModels: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { apiClient } from "@/services/api/client";

function endpoint(overrides: Partial<AiEndpoint> = {}): AiEndpoint {
  return {
    id: "ep-1",
    name: "Workshop PC",
    base_url: "http://192.168.1.50:11434/v1",
    api_key_set: false,
    timeout_seconds: 120,
    used_by: [],
    context_limit: null,
    json_mode: "auto",
    price_input_per_million: null,
    price_output_per_million: null,
    ...overrides,
  };
}

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onDirtyChange = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <AiEndpointsSection onDirtyChange={onDirtyChange} />
    </QueryClientProvider>,
  );
  return { onDirtyChange };
}

describe("AiEndpointsSection", () => {
  beforeEach(() => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([]);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("lists each endpoint with whether a key is stored and which tasks use it", async () => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([
      endpoint({ api_key_set: true, used_by: ["extraction", "filing"] }),
      endpoint({ id: "ep-2", name: "Infomaniak", base_url: "https://api.infomaniak.com/v1" }),
    ]);
    renderSection();

    expect(await screen.findByText("Workshop PC")).toBeInTheDocument();
    expect(screen.getByText("http://192.168.1.50:11434/v1")).toBeInTheDocument();
    expect(screen.getByText("Key saved")).toBeInTheDocument();
    expect(screen.getByText("Extraction")).toBeInTheDocument();
    expect(screen.getByText("Filing")).toBeInTheDocument();
    expect(screen.getByText("Infomaniak")).toBeInTheDocument();
  });

  it("adds an endpoint with the typed name, URL and key", async () => {
    const user = userEvent.setup();
    renderSection();
    await screen.findByRole("button", { name: /add endpoint/i });

    await user.click(screen.getByRole("button", { name: /add endpoint/i }));
    await user.type(screen.getByLabelText("Name"), "Workshop PC");
    await user.type(screen.getByLabelText("Endpoint URL"), "http://192.168.1.50:11434/v1");
    await user.type(screen.getByLabelText("API key"), "sk-secret-value");

    vi.mocked(apiClient.createAiEndpoint).mockResolvedValue(endpoint({ api_key_set: true }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.createAiEndpoint).toHaveBeenCalled());
    expect(vi.mocked(apiClient.createAiEndpoint).mock.calls[0]?.[0]).toEqual({
      name: "Workshop PC",
      base_url: "http://192.168.1.50:11434/v1",
      timeout_seconds: 120,
      context_limit: null,
      json_mode: "auto",
      price_input_per_million: null,
      price_output_per_million: null,
      api_key: "sk-secret-value",
    });
  });

  it("saves a longer reply timeout and refuses one out of range", async () => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([endpoint()]);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Edit Workshop PC" }));
    const timeout = screen.getByLabelText("Reply timeout (seconds)");
    expect(timeout).toHaveValue(120);

    await user.clear(timeout);
    await user.type(timeout, "5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Between 10 and 900 seconds.")).toBeInTheDocument();
    expect(apiClient.updateAiEndpoint).not.toHaveBeenCalled();

    vi.mocked(apiClient.updateAiEndpoint).mockResolvedValue(endpoint({ timeout_seconds: 300 }));
    await user.clear(timeout);
    await user.type(timeout, "300");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.updateAiEndpoint).toHaveBeenCalled());
    expect(vi.mocked(apiClient.updateAiEndpoint).mock.calls[0]?.[1]).toMatchObject({
      timeout_seconds: 300,
    });
  });

  it("saves a context limit and prices, and blank clears them back to unknown", async () => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([
      endpoint({ context_limit: 4096, price_output_per_million: 2 }),
    ]);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Edit Workshop PC" }));
    const context = screen.getByLabelText("Context limit (tokens)");
    expect(context).toHaveValue(4096);

    await user.clear(context);
    await user.type(context, "512");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Between 1,024 and 2,000,000 tokens, or blank.")).toBeVisible();
    expect(apiClient.updateAiEndpoint).not.toHaveBeenCalled();

    await user.clear(context);
    await user.type(context, "16384");
    await user.type(screen.getByLabelText("Input"), "0.4");
    await user.clear(screen.getByLabelText("Output"));
    vi.mocked(apiClient.updateAiEndpoint).mockResolvedValue(endpoint({ context_limit: 16384 }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.updateAiEndpoint).toHaveBeenCalled());
    expect(vi.mocked(apiClient.updateAiEndpoint).mock.calls[0]?.[1]).toMatchObject({
      context_limit: 16384,
      price_input_per_million: 0.4,
      price_output_per_million: null,
    });
  });

  it("shows the context limit and prices on the list", async () => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([
      endpoint({
        context_limit: 16384,
        price_input_per_million: 0.4,
        price_output_per_million: 1.6,
      }),
    ]);
    renderSection();

    expect(await screen.findByText(`${(16384).toLocaleString()} tokens context`)).toBeVisible();
    expect(screen.getByText("0.4 in · 1.6 out per 1M tokens")).toBeVisible();
  });

  it("omits a blank key from an edit, so the stored one survives a rename", async () => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([endpoint({ api_key_set: true })]);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Edit Workshop PC" }));
    // The key field starts empty because the API never hands the key back (CLAUDE.md rule 5),
    // so "blank" here can only mean "leave it alone" -- never "clear it".
    expect(screen.getByLabelText("API key")).toHaveValue("");
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Renamed");

    vi.mocked(apiClient.updateAiEndpoint).mockResolvedValue(
      endpoint({ name: "Renamed", api_key_set: true }),
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.updateAiEndpoint).toHaveBeenCalled());
    const [id, body] = vi.mocked(apiClient.updateAiEndpoint).mock.calls[0] ?? [];
    expect(id).toBe("ep-1");
    expect(body).toEqual({
      name: "Renamed",
      base_url: "http://192.168.1.50:11434/v1",
      timeout_seconds: 120,
      context_limit: null,
      json_mode: "auto",
      price_input_per_million: null,
      price_output_per_million: null,
    });
    expect(body).not.toHaveProperty("api_key");
  });

  it("tests the URL currently typed in, against the saved endpoint's stored key", async () => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([endpoint({ api_key_set: true })]);
    vi.mocked(apiClient.listAiModels).mockResolvedValue({ models: ["qwen3", "llama3"] });
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Edit Workshop PC" }));
    const url = screen.getByLabelText("Endpoint URL");
    await user.clear(url);
    await user.type(url, "http://192.168.1.51:11434/v1");
    await user.click(screen.getByRole("button", { name: /test connection/i }));

    await waitFor(() => expect(apiClient.listAiModels).toHaveBeenCalled());
    expect(vi.mocked(apiClient.listAiModels).mock.calls[0]?.[0]).toEqual({
      base_url: "http://192.168.1.51:11434/v1",
      endpoint_id: "ep-1",
    });
  });

  it("refuses to save a URL with no scheme, without calling the API", async () => {
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole("button", { name: /add endpoint/i }));
    await user.type(screen.getByLabelText("Name"), "Workshop PC");
    await user.type(screen.getByLabelText("Endpoint URL"), "192.168.1.50:11434");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Must start with http:// or https://.")).toBeInTheDocument();
    expect(apiClient.createAiEndpoint).not.toHaveBeenCalled();
  });

  it("confirms before removing, and surfaces the backend's refusal when a task still uses it", async () => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([endpoint({ used_by: ["filing"] })]);
    vi.mocked(apiClient.deleteAiEndpoint).mockRejectedValue(
      new ApiError(
        "validation_error",
        "'Workshop PC' is still assigned to the filing task. Remove it there first.",
        422,
      ),
    );
    const { toast } = await import("sonner");
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Remove Workshop PC" }));
    expect(apiClient.deleteAiEndpoint).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "'Workshop PC' is still assigned to the filing task. Remove it there first.",
      ),
    );
    expect(screen.getByText("Workshop PC")).toBeInTheDocument();
  });

  it("reports an open form as unsaved work so the navigation guard arms", async () => {
    const user = userEvent.setup();
    const { onDirtyChange } = renderSection();
    await screen.findByRole("button", { name: /add endpoint/i });

    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    await user.click(screen.getByRole("button", { name: /add endpoint/i }));
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });
});
