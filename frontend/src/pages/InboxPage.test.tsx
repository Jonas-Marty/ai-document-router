import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  Configuration,
  InboxCard,
  InboxGroup,
  InboxPage as InboxPageData,
  InboxQuery,
} from "@/services/api/types";
import InboxPage from "./InboxPage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    getInbox: vi.fn(),
    getThumbnailUrl: (id: string) => `/thumb/${id}`,
    analyseDocuments: vi.fn(),
    trashDocuments: vi.fn(),
    estimateAnalysis: vi.fn(),
    listConfigurations: vi.fn(),
    getEndpointStatuses: vi.fn(),
    revertHistoryEntry: vi.fn(),
    retryFailedProposals: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { apiClient } from "@/services/api/client";

function card(id: string, overrides: Partial<InboxCard> = {}): InboxCard {
  return {
    id,
    original_filename: `scan_${id}.pdf`,
    name: `2026.05.13 WIR Bank Kontoauszug ${id}.pdf`,
    folder: "/Finance/WIR Bank/2026",
    sender: "WIR Bank",
    document_date: "2026-05-13",
    scanned_at: "2026-05-18T10:00:00Z",
    configuration_name: "Daily",
    proposal_status: "ready",
    later: false,
    flags: [],
    analysis_stage: null,
    waiting_reason: null,
    error: null,
    history_entry_id: null,
    revertible: false,
    processed_at: null,
    ...overrides,
  };
}

const COUNTS: Record<InboxGroup, number> = { analyse: 2, ready: 2, done: 1, trashed: 0 };

function page(items: InboxCard[], extra: Partial<InboxPageData> = {}): InboxPageData {
  return {
    items,
    total: items.length,
    counts: COUNTS,
    senders: ["WIR Bank"],
    folders: ["/Finance/WIR Bank/2026"],
    analysing: 0,
    ...extra,
  };
}

const DAILY: Configuration = {
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
    max_tokens: 2048,
    thinking: "default",
  },
  filing_strategy: "one_call",
  filing: {
    endpoint_id: "ep1",
    endpoint_name: "Infomaniak",
    model_name: "big",
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

function renderPage(path = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <InboxPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function lastQuery(): InboxQuery | undefined {
  return vi.mocked(apiClient.getInbox).mock.lastCall?.[0];
}

describe("InboxPage", () => {
  beforeEach(() => {
    vi.mocked(apiClient.getInbox).mockImplementation(async (query) =>
      query.group === "done"
        ? page([card("f1", { history_entry_id: "h1", revertible: true })])
        : page([
            card("a", { flags: ["new_sender", "unusual_folder"] }),
            card("b", { later: true }),
          ]),
    );
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([DAILY]);
    vi.mocked(apiClient.getEndpointStatuses).mockResolvedValue([
      { id: "ep1", name: "Infomaniak", reachable: false },
    ]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("opens on Ready for review with counts, flags, the Later mark and a link to review", async () => {
    renderPage();

    const grid = await screen.findByRole("list", { name: "Ready for review" });
    expect(screen.getByRole("tab", { name: /ready for review/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: /to analyse/i })).toHaveTextContent("2");
    expect(within(grid).getByText("New sender")).toBeInTheDocument();
    expect(within(grid).getByText("Unusual folder")).toBeInTheDocument();
    expect(within(grid).getByText("Later")).toBeInTheDocument();
    expect(
      within(grid).getByRole("link", { name: "2026.05.13 WIR Bank Kontoauszug a.pdf" }),
    ).toHaveAttribute("href", "/review/a");
    expect(lastQuery()).toMatchObject({ group: "ready", sort: "newest", offset: 0 });
  });

  it("searches after a pause in typing", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("list", { name: "Ready for review" });

    await user.type(screen.getByLabelText("Search"), "swisscom");

    await waitFor(() => expect(lastQuery()?.q).toBe("swisscom"));
  });

  it("says nothing matches and clears the filters", async () => {
    vi.mocked(apiClient.getInbox).mockImplementation(async (query) =>
      query.q ? page([]) : page([card("a")]),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("list", { name: "Ready for review" });

    await user.type(screen.getByLabelText("Search"), "nothing");
    await user.click(await screen.findByRole("button", { name: "Clear filters" }));

    expect(screen.getByLabelText("Search")).toHaveValue("");
    expect(await screen.findByRole("list", { name: "Ready for review" })).toBeInTheDocument();
  });

  it("reverts from the Done tab after confirming, and says what waits elsewhere", async () => {
    vi.mocked(apiClient.revertHistoryEntry).mockResolvedValue({
      history_entry: {
        id: "h1",
        document_id: "f1",
        original_filename: "scan_f1.pdf",
        final_filename: "x.pdf",
        final_folder_path: "/Finance",
        action: "moved",
        was_overridden: false,
        processed_at: "2026-05-18T10:00:00Z",
        revertible: false,
      },
      document: {} as never,
    });
    const user = userEvent.setup();
    renderPage("/?tab=done");

    const grid = await screen.findByRole("list", { name: "Done" });
    expect(screen.getByRole("link", { name: "2 ready for review" })).toBeInTheDocument();
    expect(within(grid).queryByRole("checkbox")).not.toBeInTheDocument();

    await user.click(within(grid).getByRole("button", { name: "Revert" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("/Finance/WIR Bank/2026");
    await user.click(within(dialog).getByRole("button", { name: "Revert" }));

    await waitFor(() => expect(apiClient.revertHistoryEntry).toHaveBeenCalledWith("h1"));
  });

  it("trashes the selection after confirming and reports each failure", async () => {
    vi.mocked(apiClient.trashDocuments).mockResolvedValue({
      trashed: 1,
      failed: [{ id: "b", message: "The Nextcloud didn't answer." }],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("list", { name: "Ready for review" });

    await user.click(screen.getByLabelText("Select all on page"));
    const toolbar = screen.getByRole("toolbar", { name: "Selected documents" });
    expect(toolbar).toHaveTextContent("2 selected");
    expect(within(toolbar).queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    await user.click(within(toolbar).getByRole("button", { name: "Trash" }));
    const dialog = await screen.findByRole("dialog");
    expect(apiClient.trashDocuments).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Trash" }));

    await waitFor(() => expect(apiClient.trashDocuments).toHaveBeenCalledWith(["a", "b"]));
    expect(toast.error).toHaveBeenCalledWith("The Nextcloud didn't answer.");
    await waitFor(() =>
      expect(screen.queryByRole("toolbar", { name: "Selected documents" })).toBeNull(),
    );
  });

  it("re-analyses the selection with each document's own configuration", async () => {
    vi.mocked(apiClient.analyseDocuments).mockResolvedValue({ queued: 1 });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("list", { name: "Ready for review" });

    await user.click(screen.getByLabelText(/Select 2026.05.13 WIR Bank Kontoauszug a.pdf/));
    await user.click(screen.getByRole("button", { name: "Re-analyse" }));

    await waitFor(() =>
      expect(apiClient.analyseDocuments).toHaveBeenCalledWith({
        document_ids: ["a"],
        configuration_id: undefined,
      }),
    );
  });

  it("estimates before analysing with a chosen configuration", async () => {
    vi.mocked(apiClient.estimateAnalysis).mockResolvedValue({
      documents: 2,
      readings_needed: 1,
      prompt_tokens: 6000,
      completion_tokens: 600,
      cost: 0.0084,
      connections: ["Infomaniak"],
    });
    vi.mocked(apiClient.analyseDocuments).mockResolvedValue({ queued: 2 });
    vi.mocked(apiClient.listConfigurations).mockResolvedValue([
      {
        ...DAILY,
        reading_method: "vision",
        reading: { ...DAILY.reading, endpoint_name: "Tower", model_name: "qwen2.5vl:7b" },
      },
    ]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("list", { name: "Ready for review" });

    await user.click(screen.getByLabelText("Select all on page"));
    await user.click(screen.getByRole("button", { name: "Analyse with…" }));
    // Both steps of the chosen configuration, not just its name.
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Tower · qwen2.5vl:7b")).toBeVisible();
    expect(within(dialog).getByText("Infomaniak · big · one call")).toBeVisible();

    const estimate = await screen.findByRole("region", { name: "Estimate" });
    await waitFor(() => expect(estimate).toHaveTextContent("2 documents, 1 needs a new reading."));
    expect(apiClient.estimateAnalysis).toHaveBeenCalledWith(["a", "b"], "c1");
    expect(estimate).toHaveTextContent("Roughly 0.0084.");
    await waitFor(() => expect(estimate).toHaveTextContent("unreachable — documents wait"));

    await user.click(screen.getByRole("button", { name: "Analyse" }));
    await waitFor(() =>
      expect(apiClient.analyseDocuments).toHaveBeenCalledWith({
        document_ids: ["a", "b"],
        configuration_id: "c1",
      }),
    );
  });

  it("shows why a document is still in To analyse", async () => {
    vi.mocked(apiClient.getInbox).mockResolvedValue(
      page([
        card("w", { proposal_status: "pending", name: null, waiting_reason: "Waiting for Tower" }),
        card("x", { proposal_status: "failed", name: null, error: "No text layer found." }),
      ]),
    );
    renderPage("/?tab=analyse");

    const grid = await screen.findByRole("list", { name: "To analyse" });
    expect(within(grid).getByText("Waiting for Tower")).toBeInTheDocument();
    expect(within(grid).getByText("No text layer found.")).toBeInTheDocument();
    expect(within(grid).getByRole("link", { name: "scan_w.pdf" })).toBeInTheDocument();
  });

  it("marks the documents being analysed right now and counts them", async () => {
    vi.mocked(apiClient.getInbox).mockResolvedValue(
      page(
        [
          card("a", {
            proposal_status: "pending",
            name: null,
            analysis_stage: "Reading page 2 of 3",
          }),
          card("w", { proposal_status: "pending", name: null }),
        ],
        { analysing: 1 },
      ),
    );
    renderPage("/?tab=analyse");

    const grid = await screen.findByRole("list", { name: "To analyse" });
    expect(screen.getByText("Analysing 1 of 2 now")).toBeVisible();
    const items = within(grid).getAllByRole("listitem", { name: /scan_/ });
    expect(within(items[0] as HTMLElement).getByText("Analysing")).toBeVisible();
    expect(within(items[0] as HTMLElement).getByText("Reading page 2 of 3")).toBeVisible();
    expect(within(items[1] as HTMLElement).queryByText("Analysing")).not.toBeInTheDocument();
  });

  it("says when nothing in To analyse is being worked on", async () => {
    vi.mocked(apiClient.getInbox).mockResolvedValue(
      page([card("w", { proposal_status: "pending", name: null })]),
    );
    renderPage("/?tab=analyse");

    expect(await screen.findByText("Nothing is being analysed right now")).toBeVisible();
  });

  it("retries every failed analysis from To analyse, and offers it only when one failed", async () => {
    vi.mocked(apiClient.getInbox).mockResolvedValue(
      page([card("x", { proposal_status: "failed", name: null, error: "Model refused." })]),
    );
    vi.mocked(apiClient.retryFailedProposals).mockResolvedValue({ retried: 3 });
    const user = userEvent.setup();
    renderPage("/?tab=analyse");

    await user.click(await screen.findByRole("button", { name: "Retry failed" }));

    await waitFor(() => expect(apiClient.retryFailedProposals).toHaveBeenCalled());
    // The server's count: failures beyond the loaded page were retried too.
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining("3 proposals"));
  });

  it("offers no Retry failed while nothing has failed", async () => {
    renderPage("/?tab=analyse");
    await screen.findByRole("list", { name: "To analyse" });

    expect(screen.queryByRole("button", { name: "Retry failed" })).not.toBeInTheDocument();
  });
});
