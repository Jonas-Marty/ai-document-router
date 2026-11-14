import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { InboxCard, InboxPage, TestSetDetail } from "@/services/api/types";
import TestSetPage from "./TestSetPage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    getTestSet: vi.fn(),
    renameTestSet: vi.fn(),
    deleteTestSet: vi.fn(),
    addTestCases: vi.fn(),
    deleteTestCase: vi.fn(),
    getInbox: vi.fn(),
    listTestSets: vi.fn(),
    listConfigurations: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { apiClient } from "@/services/api/client";

const SET: TestSetDetail = {
  id: "set1",
  name: "Bank statements",
  case_count: 1,
  created_at: "2026-11-14T09:00:00Z",
  cases: [
    {
      id: "case1",
      source: "done",
      path: "/Documents/Finance/WIR Bank/2026/2026.05.13 WIR Bank Kontoauszug.pdf",
      original_filename: "scan_0001.pdf",
      expected_name: "2026.05.13 WIR Bank Kontoauszug",
      expected_date: "2026-05-13",
      expected_fields: null,
      expected_folder: "/Documents/Finance/WIR Bank/2026",
    },
  ],
};

function card(id: string, name: string, folder: string | null): InboxCard {
  return {
    id,
    original_filename: `${id}.pdf`,
    name,
    folder,
    sender: null,
    document_date: null,
    scanned_at: "2026-11-01T09:00:00Z",
    configuration_name: null,
    proposal_status: "ready",
    later: false,
    flags: [],
    analysis_stage: null,
    waiting_reason: null,
    error: null,
    history_entry_id: "h",
    revertible: true,
    processed_at: "2026-11-02T09:00:00Z",
  };
}

function inboxPage(items: InboxCard[]): InboxPage {
  return {
    items,
    total: items.length,
    counts: { ready: 0, analyse: 0, done: items.length, trashed: 0 },
    senders: [],
    folders: [],
    analysing: 0,
  };
}

function renderSet() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/lab/test-sets/set1"]}>
        <Routes>
          <Route path="/lab/test-sets/:id" element={<TestSetPage />} />
          <Route path="/lab/test-sets" element={<p>All sets</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("TestSetPage", () => {
  beforeEach(() => {
    vi.mocked(apiClient.getTestSet).mockResolvedValue(SET);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("lists each document with its correct name and folder", async () => {
    renderSet();

    expect(await screen.findByText("2026.05.13 WIR Bank Kontoauszug")).toBeVisible();
    expect(screen.getByText("/Documents/Finance/WIR Bank/2026")).toBeVisible();
    expect(screen.getByText("Filed · scanned as scan_0001.pdf")).toBeVisible();
  });

  it("adds the ticked Done documents", async () => {
    vi.mocked(apiClient.getInbox).mockResolvedValue(
      inboxPage([
        card("d1", "2026.04.01 Swisscom Rechnung", "/Documents/Bills"),
        card("d2", "2026.03.02 Helvetia Police", "/Documents/Insurance"),
      ]),
    );
    vi.mocked(apiClient.addTestCases).mockResolvedValue({ added: 1, skipped: 0 });
    const user = userEvent.setup();
    renderSet();

    await user.click(await screen.findByRole("button", { name: "Add from Done" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(
      await within(dialog).findByRole("checkbox", { name: /2026.04.01 Swisscom Rechnung/ }),
    );
    await user.click(within(dialog).getByRole("button", { name: "Add 1" }));

    await waitFor(() =>
      expect(apiClient.addTestCases).toHaveBeenCalledWith("set1", { document_ids: ["d1"] }),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added 1 document"));
    expect(apiClient.getInbox).toHaveBeenCalledWith(expect.objectContaining({ group: "done" }));
  });

  it("removes a document from the set", async () => {
    vi.mocked(apiClient.deleteTestCase).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderSet();

    await user.click(
      await screen.findByRole("button", { name: "Remove 2026.05.13 WIR Bank Kontoauszug" }),
    );

    await waitFor(() => expect(apiClient.deleteTestCase).toHaveBeenCalledWith("case1"));
  });

  it("refuses an empty name before asking the backend", async () => {
    const user = userEvent.setup();
    renderSet();

    const name = await screen.findByLabelText("Name");
    await user.clear(name);
    await user.type(name, "   ");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Give the set a name.")).toBeVisible();
    expect(apiClient.renameTestSet).not.toHaveBeenCalled();
  });
});
