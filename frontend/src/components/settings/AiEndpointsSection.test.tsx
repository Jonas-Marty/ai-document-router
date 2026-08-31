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
    used_by: [],
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
      api_key: "sk-secret-value",
    });
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
    expect(body).toEqual({ name: "Renamed", base_url: "http://192.168.1.50:11434/v1" });
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
