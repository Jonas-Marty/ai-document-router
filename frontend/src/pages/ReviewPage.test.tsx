import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Document, FolderContext, Settings } from "@/services/api/types";
import ReviewPage from "./ReviewPage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    getQueue: vi.fn(),
    getFolderContext: vi.fn(),
    getSettings: vi.fn(),
    getDocumentContentUrl: (id: string) => `/api/v1/documents/${id}/content`,
    retryFailedProposals: vi.fn(),
    compareDocument: vi.fn(),
  },
}));

import { apiClient } from "@/services/api/client";

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
    proposal: {
      suggested_name: suggestedName,
      target_folder_path: "/Documents/Finance",
      document_date: "2026-05-18",
      confidence_score: 0.9,
      reasoning_text: "Invoice header.",
      model_name: "test-model",
      prompt_text: null,
      system_prompt: "You file scanned documents.",
    },
    proposal_error: null,
    ocr_status: "not_needed",
    ocr_error: null,
    ...overrides,
  };
}

const SETTINGS: Settings = {
  allowed_root_folders: ["/Documents"],
  trash_folder_path: "/Trash",
  filename_pattern: null,
  filename_pattern_hint: null,
  ai_endpoint_url: "https://ai.example.com/v1",
  ai_model_name: "test-model",
  vision_model_names: [],
  store_ocr_text: true,
  ai_api_key_set: true,
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
          method: "vision",
          model_name: "qwen2.5vl:7b",
          label: "Vision · qwen2.5vl:7b",
          text_preview: "",
          proposal: {
            suggested_name: "2026.04.16 Helvetia Police",
            target_folder_path: "/Documents/Insurance",
            document_date: "2026-04-16",
            confidence_score: 0.95,
            reasoning_text: "Reads the letterhead directly.",
            model_name: "qwen2.5vl:7b",
            prompt_text: null,
            system_prompt: "You file scanned documents.",
          },
          error: null,
          duration_ms: 3100,
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
});
