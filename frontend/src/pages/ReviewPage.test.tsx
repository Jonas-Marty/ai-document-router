import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Configuration, Document, FolderContext, Settings } from "@/services/api/types";
import ReviewPage from "./ReviewPage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    getQueue: vi.fn(),
    getFolderContext: vi.fn(),
    getSettings: vi.fn(),
    getDocumentContentUrl: (id: string) => `/api/v1/documents/${id}/content`,
    retryFailedProposals: vi.fn(),
    compareDocument: vi.fn(),
    regenerateDocument: vi.fn(),
    listConfigurations: vi.fn(),
  },
}));

import { apiClient } from "@/services/api/client";

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
    filing_strategy: "one_call",
    filing: { ...step, endpoint_id: "ep1", endpoint_name: "Workshop PC", model_name: "qwen3" },
    folder_system_prompt: null,
    max_examples: 8,
    max_folders: 250,
    max_text_chars: 6000,
  };
}

function doc(id: string, suggestedName: string, overrides: Partial<Document> = {}): Document {
  return {
    id,
    original_filename: `scan_${id}.pdf`,
    extension: ".pdf",
    mime_type: "application/pdf",
    file_size_bytes: 1200,
    page_count: 1,
    scanned_at: "2026-05-18T10:00:00Z",
    status: "pending",
    skip_count: 0,
    proposal_status: "ready",
    analysis_stage: null,
    proposal: {
      suggested_name: suggestedName,
      fields: null,
      target_folder_path: "/Documents/Finance",
      document_date: "2026-05-18",
      confidence_score: 0.9,
      reasoning_text: "Invoice header.",
      model_name: "test-model",
      connection_name: null,
      configuration_name: null,
      reading_label: null,
      prompt_text: null,
      system_prompt: "You file scanned documents.",
    },
    proposal_error: null,
    ocr_status: "not_needed",
    ocr_error: null,
    analysis_usage: null,
    waiting_reason: null,
    configuration_name: null,
    reading: null,
    ...overrides,
  };
}

const SETTINGS: Settings = {
  allowed_root_folders: ["/Documents"],
  trash_folder_path: "/Trash",
  watch_folder_path: "/Scans/Inbox",
  filename_pattern: null,
  filename_pattern_hint: null,
  store_ocr_text: true,
  proposal_batch_size: 10,
};

const FOLDER_CONTEXT: FolderContext = {
  path: "/Documents/Finance",
  exists: true,
  siblings: [],
  total_file_count: 0,
  filename_collision: false,
};

function renderReview(items: Document[], totalPending = items.length) {
  vi.mocked(apiClient.getQueue).mockResolvedValue({ items, total_pending: totalPending });
  vi.mocked(apiClient.getSettings).mockResolvedValue(SETTINGS);
  vi.mocked(apiClient.getFolderContext).mockResolvedValue(FOLDER_CONTEXT);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ReviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ReviewPage queue overview", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("says how many documents are still open without opening anything", async () => {
    renderReview([doc("1", "2026.05.18 Swisscom Rechnung")], 12);

    const trigger = await screen.findByRole("button", { name: /queue/i });
    expect(trigger).toHaveTextContent("12");
  });

  it("offers no queue control when nothing is waiting", async () => {
    renderReview([], 0);

    expect(await screen.findByText("Queue's clear.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^queue/i })).not.toBeInTheDocument();
  });

  it("retries every failed proposal at once, not one document at a time", async () => {
    // The whole point: the poller never revisits a failed proposal, so a configuration fix
    // in Settings cannot heal a queue that already failed against the old configuration.
    renderReview([
      doc("1", "2026.05.18 Swisscom Rechnung"),
      doc("2", "", {
        proposal_status: "failed",
        proposal: null,
        proposal_error: "No allowed folders are configured yet — set them in Settings.",
      }),
    ]);
    vi.mocked(apiClient.retryFailedProposals).mockResolvedValue({ retried: 2 });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /queue/i }));
    await user.click(await screen.findByRole("button", { name: /retry failed/i }));

    await waitFor(() => expect(apiClient.retryFailedProposals).toHaveBeenCalled());
  });

  it("fills the review form from the comparison method that was picked", async () => {
    renderReview([doc("1", "2026.05.18 Swisscom Rechnung")]);
    vi.mocked(apiClient.compareDocument).mockResolvedValue({
      results: [
        {
          method: "markdown",
          model_name: "qwen2.5vl:7b",
          label: "Markdown · Local · qwen2.5vl:7b",
          text_preview: "",
          proposal: {
            suggested_name: "2026.04.16 Helvetia Police",
            fields: null,
            target_folder_path: "/Documents/Insurance",
            document_date: "2026-04-16",
            confidence_score: 0.95,
            reasoning_text: "Reads the letterhead directly.",
            model_name: "qwen2.5vl:7b",
            connection_name: null,
            configuration_name: null,
            reading_label: null,
            prompt_text: null,
            system_prompt: "You file scanned documents.",
          },
          error: null,
          duration_ms: 3100,
          usage: null,
        },
      ],
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /compare methods/i }));
    await user.click(await screen.findByRole("button", { name: /use this/i }));

    // Filled in, not approved -- and still editable, because "nearly right" is the point
    // of having a review step at all.
    await waitFor(() =>
      expect(screen.getByDisplayValue("2026.04.16 Helvetia Police")).toBeInTheDocument(),
    );
    expect(screen.getByDisplayValue("2026-04-16")).toBeInTheDocument();
  });

  it("lists every open document and jumps to the one that is picked", async () => {
    renderReview([
      doc("1", "2026.05.18 Swisscom Rechnung"),
      doc("2", "2026.05.19 SAC Spendenbescheinigung"),
    ]);
    const user = userEvent.setup();

    // The review form starts on the first document.
    expect(await screen.findByDisplayValue("2026.05.18 Swisscom Rechnung")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /queue/i }));
    await user.click(
      await screen.findByRole("button", { name: /2026.05.19 SAC Spendenbescheinigung/ }),
    );

    // Picking closes the panel and the form is now editing the document that was picked.
    await waitFor(() =>
      expect(screen.getByDisplayValue("2026.05.19 SAC Spendenbescheinigung")).toBeInTheDocument(),
    );
  });

  it("shows the last comparison when reopened instead of paying for another run", async () => {
    renderReview([doc("1", "2026.05.18 Swisscom Rechnung")]);
    vi.mocked(apiClient.compareDocument).mockResolvedValue({ results: [] });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /compare methods/i }));
    await waitFor(() => expect(apiClient.compareDocument).toHaveBeenCalledTimes(1));
    await user.keyboard("{Escape}");
    await user.click(await screen.findByRole("button", { name: /compare methods/i }));
    expect(apiClient.compareDocument).toHaveBeenCalledTimes(1);

    await user.click(await screen.findByRole("button", { name: /run again/i }));
    await waitFor(() => expect(apiClient.compareDocument).toHaveBeenCalledTimes(2));
  });

  it("says which step the analysis is on while the proposal is being made", async () => {
    renderReview([
      doc("1", "scan.pdf", {
        proposal_status: "pending",
        proposal: null,
        analysis_stage: "Reading the document with Jonas-PC · chandra-ocr-2…",
      }),
    ]);

    expect(
      await screen.findByText("Reading the document with Jonas-PC · chandra-ocr-2…"),
    ).toBeInTheDocument();
  });

  it("re-runs with the configuration picked in the dialog", async () => {
    // After a configuration changed in the Lab, a ready proposal is stale.
    renderReview([doc("1", "2026.05.18 Swisscom Rechnung")]);
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([
      configuration("c1", "Daily", true),
      configuration("c2", "Careful", false),
    ]);
    vi.mocked(apiClient.regenerateDocument).mockResolvedValue(
      doc("1", "2026.05.18 Swisscom Rechnung", { proposal_status: "pending", proposal: null }),
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /re-run analysis/i }));
    const dialog = await screen.findByRole("dialog");

    // Starts on the default, which this document follows, so confirming alone repeats it.
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: /configuration/i })).toHaveTextContent(
        "Daily (default)",
      ),
    );
    await user.click(within(dialog).getByRole("combobox", { name: /configuration/i }));
    await user.click(await screen.findByRole("option", { name: "Careful" }));
    await user.click(within(dialog).getByRole("button", { name: /run analysis/i }));

    await waitFor(() =>
      expect(apiClient.regenerateDocument).toHaveBeenCalledWith("1", { configuration_id: "c2" }),
    );
  });

  it("starts the dialog on the configuration picked for the document earlier", async () => {
    renderReview([doc("1", "2026.05.18 Swisscom Rechnung", { configuration_name: "Careful" })]);
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([
      configuration("c1", "Daily", true),
      configuration("c2", "Careful", false),
    ]);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /re-run analysis/i }));
    const dialog = await screen.findByRole("dialog");

    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: /configuration/i })).toHaveTextContent(
        "Careful",
      ),
    );
  });

  it("says why a document is waiting rather than showing it as in progress", async () => {
    renderReview([
      doc("1", "unused", {
        proposal_status: "pending",
        proposal: null,
        waiting_reason: "Waiting for Tower to read",
      }),
    ]);

    expect(await screen.findAllByText("Waiting for Tower to read")).not.toHaveLength(0);
  });
});
