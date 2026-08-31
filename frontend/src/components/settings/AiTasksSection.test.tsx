import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/services/api/errors";
import type { AiEndpoint, AiTaskChain } from "@/services/api/types";
import { AiTasksSection } from "./AiTasksSection";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    listAiEndpoints: vi.fn(),
    listAiTasks: vi.fn(),
    updateAiTask: vi.fn(),
    listAiModels: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { apiClient } from "@/services/api/client";

const LOCAL: AiEndpoint = {
  id: "ep-local",
  name: "Workshop PC",
  base_url: "http://192.168.1.50:11434/v1",
  api_key_set: false,
  used_by: [],
};
const HOSTED: AiEndpoint = {
  id: "ep-hosted",
  name: "Infomaniak",
  base_url: "https://api.infomaniak.com/v1",
  api_key_set: true,
  used_by: [],
};

const EMPTY_CHAINS: AiTaskChain[] = [
  { task: "extraction", steps: [] },
  { task: "filing", steps: [] },
];

/** The card for one task, so a query can't accidentally match the other one's controls. */
function card(title: string): HTMLElement {
  const form = screen.getByText(title, { selector: "div" }).closest("form");
  if (form === null) throw new Error(`No form around the '${title}' card.`);
  return form;
}

const FILING = "Filing — choose the filename";
const EXTRACTION = "Extraction — read the pages";

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onDirtyChange = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <AiTasksSection onDirtyChange={onDirtyChange} />
    </QueryClientProvider>,
  );
  return { onDirtyChange };
}

describe("AiTasksSection", () => {
  beforeEach(() => {
    vi.mocked(apiClient.listAiEndpoints).mockResolvedValue([LOCAL, HOSTED]);
    vi.mocked(apiClient.listAiTasks).mockResolvedValue(EMPTY_CHAINS);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("shows a card per task, and says what an unassigned task falls back to", async () => {
    renderSection();

    expect(await screen.findByText(EXTRACTION)).toBeInTheDocument();
    expect(screen.getByText(FILING)).toBeInTheDocument();
    expect(screen.getByText(/read from the PDF's own text layer instead/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing will be proposed until one is/i)).toBeInTheDocument();
  });

  it("labels a saved chain by priority, first choice before fallbacks", async () => {
    vi.mocked(apiClient.listAiTasks).mockResolvedValue([
      { task: "extraction", steps: [] },
      {
        task: "filing",
        steps: [
          { endpoint_id: LOCAL.id, endpoint_name: LOCAL.name, model_name: "qwen3" },
          { endpoint_id: HOSTED.id, endpoint_name: HOSTED.name, model_name: "gpt-4o" },
        ],
      },
    ]);
    renderSection();

    await screen.findByText(FILING);
    const filing = within(card(FILING));
    expect(filing.getByText("First choice")).toBeInTheDocument();
    expect(filing.getByText("Fallback 1")).toBeInTheDocument();
    // The chain is an ordered list, so the rows have to read in the order they are tried.
    expect(
      filing.getAllByRole("combobox", { name: "Endpoint" }).map((el) => el.textContent),
    ).toEqual(["Workshop PC", "Infomaniak"]);
    expect(filing.getAllByRole("combobox", { name: "Model" }).map((el) => el.textContent)).toEqual([
      "qwen3",
      "gpt-4o",
    ]);
  });

  it("adds a step, picks an endpoint and model, and saves the chain in order", async () => {
    vi.mocked(apiClient.listAiModels).mockResolvedValue({ models: ["qwen3", "llama3"] });
    const user = userEvent.setup();
    renderSection();
    await screen.findByText(FILING);

    const filing = within(card(FILING));
    await user.click(filing.getByRole("button", { name: /add endpoint/i }));
    await user.click(filing.getByRole("combobox", { name: "Endpoint" }));
    await user.click(await screen.findByRole("option", { name: "Workshop PC" }));

    // Nothing is fetched until a model dropdown is actually opened -- these are live calls
    // to somebody else's server, not something to do on render.
    expect(apiClient.listAiModels).not.toHaveBeenCalled();
    await user.click(filing.getByRole("combobox", { name: "Model" }));
    await waitFor(() => expect(apiClient.listAiModels).toHaveBeenCalled());
    expect(vi.mocked(apiClient.listAiModels).mock.calls[0]?.[0]).toEqual({
      base_url: LOCAL.base_url,
      endpoint_id: LOCAL.id,
    });
    await user.click(await screen.findByRole("option", { name: "qwen3" }));

    vi.mocked(apiClient.updateAiTask).mockResolvedValue({
      task: "filing",
      steps: [{ endpoint_id: LOCAL.id, endpoint_name: LOCAL.name, model_name: "qwen3" }],
    });
    await user.click(filing.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.updateAiTask).toHaveBeenCalled());
    expect(vi.mocked(apiClient.updateAiTask).mock.calls[0]).toEqual([
      "filing",
      { steps: [{ endpoint_id: LOCAL.id, model_name: "qwen3" }] },
    ]);
  });

  it("moves a fallback ahead of the first choice and saves the new order", async () => {
    vi.mocked(apiClient.listAiTasks).mockResolvedValue([
      { task: "extraction", steps: [] },
      {
        task: "filing",
        steps: [
          { endpoint_id: LOCAL.id, endpoint_name: LOCAL.name, model_name: "qwen3" },
          { endpoint_id: HOSTED.id, endpoint_name: HOSTED.name, model_name: "gpt-4o" },
        ],
      },
    ]);
    const user = userEvent.setup();
    renderSection();
    await screen.findByText(FILING);

    const filing = within(card(FILING));
    await user.click(filing.getByRole("button", { name: "Move step 2 up" }));

    vi.mocked(apiClient.updateAiTask).mockResolvedValue({ task: "filing", steps: [] });
    await user.click(filing.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.updateAiTask).toHaveBeenCalled());
    expect(vi.mocked(apiClient.updateAiTask).mock.calls[0]?.[1]).toEqual({
      steps: [
        { endpoint_id: HOSTED.id, model_name: "gpt-4o" },
        { endpoint_id: LOCAL.id, model_name: "qwen3" },
      ],
    });
  });

  it("saves an emptied chain, which is how a task is switched off", async () => {
    vi.mocked(apiClient.listAiTasks).mockResolvedValue([
      {
        task: "extraction",
        steps: [{ endpoint_id: LOCAL.id, endpoint_name: LOCAL.name, model_name: "got-ocr" }],
      },
      { task: "filing", steps: [] },
    ]);
    const user = userEvent.setup();
    renderSection();
    await screen.findByText(EXTRACTION);

    const extraction = within(card(EXTRACTION));
    await user.click(extraction.getByRole("button", { name: "Remove step 1" }));

    vi.mocked(apiClient.updateAiTask).mockResolvedValue({ task: "extraction", steps: [] });
    await user.click(extraction.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.updateAiTask).toHaveBeenCalled());
    expect(vi.mocked(apiClient.updateAiTask).mock.calls[0]).toEqual(["extraction", { steps: [] }]);
  });

  it("refuses to save a step with no model chosen", async () => {
    const user = userEvent.setup();
    renderSection();
    await screen.findByText(FILING);

    const filing = within(card(FILING));
    await user.click(filing.getByRole("button", { name: /add endpoint/i }));
    await user.click(filing.getByRole("combobox", { name: "Endpoint" }));
    await user.click(await screen.findByRole("option", { name: "Workshop PC" }));
    await user.click(filing.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Choose a model.")).toBeInTheDocument();
    expect(apiClient.updateAiTask).not.toHaveBeenCalled();
  });

  it("lets a model name be typed when the endpoint cannot be reached", async () => {
    vi.mocked(apiClient.listAiModels).mockRejectedValue(
      new ApiError("ai_unavailable", "Couldn't reach the AI endpoint: connection refused.", 503),
    );
    const user = userEvent.setup();
    renderSection();
    await screen.findByText(FILING);

    const filing = within(card(FILING));
    await user.click(filing.getByRole("button", { name: /add endpoint/i }));
    await user.click(filing.getByRole("combobox", { name: "Endpoint" }));
    await user.click(await screen.findByRole("option", { name: "Workshop PC" }));

    await user.click(filing.getByRole("button", { name: /enter a model name manually/i }));
    await user.type(filing.getByLabelText("Model"), "custom/model");

    vi.mocked(apiClient.updateAiTask).mockResolvedValue({ task: "filing", steps: [] });
    await user.click(filing.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.updateAiTask).toHaveBeenCalled());
    expect(vi.mocked(apiClient.updateAiTask).mock.calls[0]?.[1]).toEqual({
      steps: [{ endpoint_id: LOCAL.id, model_name: "custom/model" }],
    });
  });

  it("reports each task's dirty state to the page", async () => {
    const user = userEvent.setup();
    const { onDirtyChange } = renderSection();
    await screen.findByText(FILING);

    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    await user.click(within(card(FILING)).getByRole("button", { name: /add endpoint/i }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
  });
});
