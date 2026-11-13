import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NavigationGuardProvider } from "@/hooks/useNavigationGuard";
import type { ArchiveKnowledge, KnownSender } from "@/services/api/types";
import KnowledgePage from "./KnowledgePage";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    getKnowledge: vi.fn(),
    updateKnowledge: vi.fn(),
    listKnownSenders: vi.fn(),
    updateKnownSender: vi.fn(),
    deleteKnownSender: vi.fn(),
    deleteKnownAccount: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { apiClient } from "@/services/api/client";
import { ApiError } from "@/services/api/errors";

const KNOWLEDGE: ArchiveKnowledge = {
  name_template: ["date", "sender", "account", "title", "period", "number"],
  filing_rules: null,
};

const ZKB: KnownSender = {
  id: "s1",
  short_name: "ZKB",
  aliases: ["Zürcher Kantonalbank"],
  accounts: [{ id: "a1", value: "CH33 0070" }],
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <NavigationGuardProvider>
        <MemoryRouter>
          <KnowledgePage />
        </MemoryRouter>
      </NavigationGuardProvider>
    </QueryClientProvider>,
  );
}

describe("KnowledgePage", () => {
  beforeEach(() => {
    vi.mocked(apiClient.getKnowledge).mockResolvedValue(KNOWLEDGE);
    vi.mocked(apiClient.listKnownSenders).mockResolvedValue([ZKB]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("reorders the template, drops an optional field, and saves only what is included", async () => {
    vi.mocked(apiClient.updateKnowledge).mockImplementation(async (body) => body);
    const user = userEvent.setup();
    renderPage();

    const example = await screen.findByLabelText("Example name");
    expect(example).toHaveTextContent(
      "2026.05.13 WIR Bank 738.953-48.5800 Kontoauszug 01.04.2026 - 30.04.2026 4711.pdf",
    );

    await user.click(screen.getByRole("button", { name: "Move Date down" }));
    const rows = screen.getAllByRole("listitem");
    await user.click(within(rows[5] as HTMLElement).getByLabelText("Include"));
    expect(example).toHaveTextContent(
      "WIR Bank 2026.05.13 738.953-48.5800 Kontoauszug 01.04.2026 - 30.04.2026.pdf",
    );

    const card = screen.getByText("Name template").closest("form") as HTMLElement;
    await user.click(within(card).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.updateKnowledge).toHaveBeenCalled());
    expect(vi.mocked(apiClient.updateKnowledge).mock.calls[0]?.[0]).toEqual({
      name_template: ["sender", "date", "account", "title", "period"],
      filing_rules: null,
    });
  });

  it("offers no way to leave out the date, sender or title", async () => {
    renderPage();

    await screen.findByLabelText("Example name");
    expect(screen.getAllByLabelText("Include")).toHaveLength(3);
    expect(screen.getAllByText("Always in")).toHaveLength(3);
  });

  it("saves filing rules, blank as null, and refuses too many characters", async () => {
    vi.mocked(apiClient.getKnowledge).mockResolvedValue({ ...KNOWLEDGE, filing_rules: "Old." });
    vi.mocked(apiClient.updateKnowledge).mockImplementation(async (body) => body);
    const user = userEvent.setup();
    renderPage();

    const rules = await screen.findByLabelText("Rules for naming and filing");
    const card = rules.closest("form") as HTMLElement;
    await user.clear(rules);
    await user.click(within(card).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiClient.updateKnowledge).toHaveBeenCalled());
    expect(vi.mocked(apiClient.updateKnowledge).mock.calls[0]?.[0].filing_rules).toBeNull();

    await user.click(rules);
    await user.paste("x".repeat(4001));
    expect(within(card).getByText("4001 / 4000")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Save" }));
    expect(apiClient.updateKnowledge).toHaveBeenCalledTimes(1);
  });

  it("lists what approvals taught, and edits how a sender is written", async () => {
    vi.mocked(apiClient.updateKnownSender).mockResolvedValue(ZKB);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Written as Zürcher Kantonalbank")).toBeInTheDocument();
    expect(screen.getByText("CH33 0070")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit ZKB" }));
    const aliases = screen.getByLabelText("Written in documents as");
    await user.type(aliases, "\n ZKB Zürich \n");
    await user.click(
      within(aliases.closest("form") as HTMLElement).getByRole("button", { name: "Save" }),
    );

    await waitFor(() =>
      expect(apiClient.updateKnownSender).toHaveBeenCalledWith("s1", {
        short_name: "ZKB",
        aliases: ["Zürcher Kantonalbank", "ZKB Zürich"],
      }),
    );
  });

  it("keeps the edit open and says why when a rename is refused", async () => {
    vi.mocked(apiClient.updateKnownSender).mockRejectedValue(
      new ApiError("validation_error", "'Rega' is already a known sender.", 422),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Edit ZKB" }));
    const name = screen.getByLabelText("Short name");
    await user.clear(name);
    await user.type(name, "Rega");
    await user.click(
      within(name.closest("form") as HTMLElement).getByRole("button", { name: "Save" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("already a known sender");
    expect(screen.getByLabelText("Short name")).toHaveValue("Rega");
  });

  it("confirms before forgetting a sender, and forgets one account directly", async () => {
    vi.mocked(apiClient.deleteKnownSender).mockResolvedValue(undefined);
    vi.mocked(apiClient.deleteKnownAccount).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Remove account CH33 0070" }));
    await waitFor(() => expect(apiClient.deleteKnownAccount).toHaveBeenCalledWith("a1"));

    await user.click(screen.getByRole("button", { name: "Remove ZKB" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/accounts go with it/)).toBeVisible();
    expect(apiClient.deleteKnownSender).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(apiClient.deleteKnownSender).toHaveBeenCalledWith("s1"));
  });

  it("says how senders are learned when there are none", async () => {
    vi.mocked(apiClient.listKnownSenders).mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText(/asks whether to remember it/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add/i })).not.toBeInTheDocument();
  });
});
