import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/services/api/errors";
import type {
  Configuration,
  Document,
  FolderContext,
  InboxCard,
  InboxPage,
  Settings,
} from "@/services/api/types";
import ReviewPage from "./ReviewPage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    getInbox: vi.fn(),
    getDocument: vi.fn(),
    getThumbnailUrl: (id: string) => `/thumb/${id}`,
    getFolderContext: vi.fn(),
    getSettings: vi.fn(),
    getDocumentContentUrl: (id: string) => `/api/v1/documents/${id}/content`,
    approveDocument: vi.fn(),
    skipDocument: vi.fn(),
    trashDocument: vi.fn(),
    regenerateDocument: vi.fn(),
    listConfigurations: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
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

function cardOf(document: Document): InboxCard {
  return {
    id: document.id,
    original_filename: document.original_filename,
    name: document.proposal ? `${document.proposal.suggested_name}.pdf` : null,
    folder: document.proposal?.target_folder_path ?? null,
    sender: null,
    document_date: null,
    scanned_at: document.scanned_at,
    configuration_name: null,
    proposal_status: document.proposal_status,
    later: document.status === "skipped",
    flags: [],
    analysis_stage: null,
    waiting_reason: null,
    error: null,
    history_entry_id: null,
    revertible: false,
    processed_at: null,
  };
}

function pageOf(list: Document[]): InboxPage {
  return {
    items: list.map(cardOf),
    total: list.length,
    counts: { analyse: 0, ready: list.length, done: 0, trashed: 0 },
    senders: [],
    folders: [],
  };
}

function Where() {
  return <output aria-label="Location">{useLocation().pathname}</output>;
}

/** `ready` is the list; `extra` are documents only reachable by URL (To analyse, filed). */
function renderReview(ready: Document[], { path = "/review", extra = [] as Document[] } = {}) {
  const byId = new Map([...ready, ...extra].map((d) => [d.id, d]));
  vi.mocked(apiClient.getInbox).mockResolvedValue(pageOf(ready));
  vi.mocked(apiClient.getDocument).mockImplementation(async (id) => {
    const found = byId.get(id);
    if (!found) throw new ApiError("not_found", "That document no longer exists.", 404);
    return found;
  });
  vi.mocked(apiClient.getSettings).mockResolvedValue(SETTINGS);
  vi.mocked(apiClient.getFolderContext).mockResolvedValue(FOLDER_CONTEXT);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/review/:id?" element={<ReviewPage />} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function location() {
  return screen.getByRole("status", { name: "Location" });
}

const SWISSCOM = doc("1", "2026.05.18 Swisscom Rechnung");
const SAC = doc("2", "2026.05.19 SAC Spendenbescheinigung");
const HELVETIA = doc("3", "2026.04.16 Helvetia Police");

function filed(document: Document): Document {
  return { ...document, status: "moved" };
}

function routed(document: Document) {
  return {
    document: filed(document),
    history_entry: {
      id: `h${document.id}`,
      document_id: document.id,
      original_filename: document.original_filename,
      final_filename: "x.pdf",
      final_folder_path: "/Documents/Finance",
      action: "moved" as const,
      was_overridden: false,
      processed_at: "2026-05-18T10:00:00Z",
      revertible: true,
    },
  };
}

describe("ReviewPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("opens the first ready row and puts it in the URL", async () => {
    renderReview([SWISSCOM, SAC]);

    expect(await screen.findByDisplayValue("2026.05.18 Swisscom Rechnung")).toBeInTheDocument();
    await waitFor(() => expect(location()).toHaveTextContent("/review/1"));
    expect(screen.getByRole("button", { name: /ready/i })).toHaveTextContent("2");
    expect(apiClient.getInbox).toHaveBeenCalledWith(expect.objectContaining({ group: "ready" }));
  });

  it("says there is nothing to review and leads back to the Inbox", async () => {
    renderReview([]);

    expect(await screen.findByText("Nothing to review.")).toBeInTheDocument();
    expect(screen.getByText("New scans appear in the Inbox automatically.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to the Inbox" })).toBeInTheDocument();
  });

  it("opens the row picked in the list, marks it current and closes the list", async () => {
    renderReview([SWISSCOM, SAC]);
    const user = userEvent.setup();
    await screen.findByDisplayValue("2026.05.18 Swisscom Rechnung");

    await user.click(screen.getByRole("button", { name: /ready/i }));
    const list = await screen.findByRole("list", { name: "Ready for review" });
    expect(within(list).getByRole("link", { name: /Swisscom/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await user.click(within(list).getByRole("link", { name: /SAC/ }));

    expect(
      await screen.findByDisplayValue("2026.05.19 SAC Spendenbescheinigung"),
    ).toBeInTheDocument();
    expect(location()).toHaveTextContent("/review/2");
    await waitFor(() =>
      expect(screen.queryByRole("list", { name: "Ready for review" })).not.toBeInTheDocument(),
    );
  });

  it("moves to the row below after an approve", async () => {
    renderReview([SWISSCOM, SAC, HELVETIA], { path: "/review/2" });
    vi.mocked(apiClient.approveDocument).mockResolvedValue(routed(SAC));
    const user = userEvent.setup();
    await screen.findByDisplayValue("2026.05.19 SAC Spendenbescheinigung");

    await user.click(screen.getByRole("button", { name: /approve & move/i }));

    await waitFor(() => expect(location()).toHaveTextContent("/review/3"));
    expect(await screen.findByDisplayValue("2026.04.16 Helvetia Police")).toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith("Moved to /Documents/Finance");
  });

  it("stays on the document with the edits kept when an approve fails", async () => {
    renderReview([SWISSCOM, SAC]);
    vi.mocked(apiClient.approveDocument).mockRejectedValue(
      new ApiError("collision", "A file with that name already exists.", 409),
    );
    const user = userEvent.setup();
    const name = await screen.findByDisplayValue("2026.05.18 Swisscom Rechnung");
    await waitFor(() => expect(location()).toHaveTextContent("/review/1"));

    await user.clear(name);
    await user.type(name, "2026.05.18 Swisscom Mobile");
    await user.click(screen.getByRole("button", { name: /approve & move/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A file with that name already exists.",
    );
    expect(location()).toHaveTextContent("/review/1");
    expect(screen.getByDisplayValue("2026.05.18 Swisscom Mobile")).toBeInTheDocument();
  });

  it("puts the last row off and moves to the one above it", async () => {
    renderReview([SWISSCOM, SAC], { path: "/review/2" });
    vi.mocked(apiClient.skipDocument).mockResolvedValue({ ...SAC, status: "skipped" });
    const user = userEvent.setup();
    await screen.findByDisplayValue("2026.05.19 SAC Spendenbescheinigung");

    await user.click(screen.getByRole("button", { name: "Later" }));

    await waitFor(() => expect(apiClient.skipDocument).toHaveBeenCalledWith("2"));
    await waitFor(() => expect(location()).toHaveTextContent("/review/1"));
  });

  it("trashes after confirming and moves on", async () => {
    renderReview([SWISSCOM, SAC]);
    vi.mocked(apiClient.trashDocument).mockResolvedValue(routed(SWISSCOM));
    const user = userEvent.setup();
    await screen.findByDisplayValue("2026.05.18 Swisscom Rechnung");

    await user.click(screen.getByRole("button", { name: "Move to trash" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Move to trash" }),
    );

    await waitFor(() => expect(location()).toHaveTextContent("/review/2"));
  });

  it("says when the document in the URL was filed meanwhile, and offers the list", async () => {
    renderReview([SWISSCOM], { path: "/review/9", extra: [filed(doc("9", "Old"))] });
    const user = userEvent.setup();

    expect(
      await screen.findByText("This document has already been filed or trashed."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review the next one" }));

    expect(await screen.findByDisplayValue("2026.05.18 Swisscom Rechnung")).toBeInTheDocument();
  });

  it("re-analyses with the document's own configuration and moves on", async () => {
    renderReview([SWISSCOM, SAC]);
    vi.mocked(apiClient.regenerateDocument).mockResolvedValue({
      ...SWISSCOM,
      proposal_status: "pending",
      proposal: null,
    });
    const user = userEvent.setup();
    await screen.findByDisplayValue("2026.05.18 Swisscom Rechnung");

    const producedBy = screen.getByRole("region", { name: "Produced by" });
    expect(producedBy).toHaveTextContent("test-model");
    await user.click(within(producedBy).getByRole("button", { name: "Re-analyse" }));

    await waitFor(() => expect(apiClient.regenerateDocument).toHaveBeenCalledWith("1", undefined));
    await waitFor(() => expect(location()).toHaveTextContent("/review/2"));
  });

  it("stays on a failed document opened from To analyse when it is tried again", async () => {
    const broken = doc("7", "", {
      proposal_status: "failed",
      proposal: null,
      proposal_error: "No text layer found.",
    });
    renderReview([SWISSCOM], { path: "/review/7", extra: [broken] });
    vi.mocked(apiClient.regenerateDocument).mockResolvedValue({
      ...broken,
      proposal_status: "pending",
      proposal_error: null,
    });
    const user = userEvent.setup();

    expect(await screen.findByText("No text layer found.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(apiClient.regenerateDocument).toHaveBeenCalledWith("7", undefined));
    expect(location()).toHaveTextContent("/review/7");
  });

  it("says which step the analysis is on while the proposal is being made", async () => {
    // Opened from To analyse: outside the list, followed by its URL.
    renderReview([SWISSCOM], {
      path: "/review/5",
      extra: [
        doc("5", "scan.pdf", {
          proposal_status: "pending",
          proposal: null,
          analysis_stage: "Reading the document with Jonas-PC · chandra-ocr-2…",
        }),
      ],
    });

    expect(
      await screen.findByText("Reading the document with Jonas-PC · chandra-ocr-2…"),
    ).toBeInTheDocument();
  });

  it("re-analyses with the configuration picked in the dialog", async () => {
    // After a configuration changed in the Lab, a ready proposal is stale.
    renderReview([SWISSCOM]);
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([
      configuration("c1", "Daily", true),
      configuration("c2", "Careful", false),
    ]);
    vi.mocked(apiClient.regenerateDocument).mockResolvedValue(
      doc("1", "2026.05.18 Swisscom Rechnung", { proposal_status: "pending", proposal: null }),
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /re-analyse with/i }));
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

    await user.click(await screen.findByRole("button", { name: /re-analyse with/i }));
    const dialog = await screen.findByRole("dialog");

    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: /configuration/i })).toHaveTextContent(
        "Careful",
      ),
    );
  });

  it("says why a document is waiting rather than showing it as in progress", async () => {
    renderReview([], {
      path: "/review/1",
      extra: [
        doc("1", "unused", {
          proposal_status: "pending",
          proposal: null,
          waiting_reason: "Waiting for Tower to read",
        }),
      ],
    });

    expect(await screen.findAllByText("Waiting for Tower to read")).not.toHaveLength(0);
  });
});
