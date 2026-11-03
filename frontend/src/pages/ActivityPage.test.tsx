import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuditEvent, AuditPage, AuditUsage } from "@/services/api/types";
import ActivityPage from "./ActivityPage";

vi.mock("@/services/api/client", () => ({
  apiClient: { getCurrentUser: vi.fn(), getAudit: vi.fn(), getAuditUsage: vi.fn() },
}));

import { apiClient } from "@/services/api/client";

const ADMIN = { id: "u1", email: "owner@example.com", is_admin: true };

function event(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: "evt-1",
    occurred_at: "2026-05-18T10:00:00+00:00",
    actor_label: "owner@example.com",
    action: "settings.updated",
    document_id: null,
    summary: "Changed the trash folder",
    details: {
      changed: { trash_folder_path: { from: "/Trash", to: "/Bin" } },
      api_key_changed: false,
    },
    prompt_tokens: null,
    completion_tokens: null,
    total_tokens: null,
    ...overrides,
  };
}

function page(items: AuditEvent[], nextCursor: string | null = null): AuditPage {
  return { items, next_cursor: nextCursor };
}

const USAGE: AuditUsage = {
  days: 30,
  analyses: 42,
  comparisons: 3,
  prompt_tokens: 120_400,
  completion_tokens: 8_200,
  total_tokens: 128_600,
  by_model: [
    {
      task: "filing",
      model_label: "Infomaniak · gpt-4o",
      prompt_tokens: 60_000,
      completion_tokens: 3_000,
      total_tokens: 63_000,
      calls: 45,
    },
  ],
};

function setMatchMedia(matches: boolean): void {
  window.matchMedia = (query: string) =>
    ({
      matches,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ActivityPage />
    </QueryClientProvider>,
  );
}

describe("ActivityPage", () => {
  beforeEach(() => {
    vi.mocked(apiClient.getCurrentUser).mockResolvedValue(ADMIN);
    vi.mocked(apiClient.getAuditUsage).mockResolvedValue(USAGE);
  });

  afterEach(() => {
    vi.clearAllMocks();
    setMatchMedia(false);
  });

  it("shows the usage summary for the last 30 days", async () => {
    vi.mocked(apiClient.getAudit).mockResolvedValue(page([event()]));
    renderPage();

    const summary = await screen.findByRole("region", { name: /ai usage · last 30 days/i });
    expect(within(summary).getByText("Analyses").nextSibling).toHaveTextContent("42");
    expect(within(summary).getByText("Tokens").nextSibling).toHaveTextContent("129K");
    expect(within(summary).getByText("Prompt · completion").nextSibling).toHaveTextContent(
      "120K · 8.2K",
    );
    const models = within(summary).getByRole("list", { name: /tokens by model/i });
    expect(models).toHaveTextContent("filing · Infomaniak · gpt-4o");
    expect(models).toHaveTextContent("63K tokens · 45 calls");
    expect(apiClient.getAuditUsage).toHaveBeenCalledWith(30);
  });

  it("renders events as cards on mobile, with the details one tap away", async () => {
    vi.mocked(apiClient.getAudit).mockResolvedValue(page([event()]));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Changed the trash folder")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    await user.click(screen.getByText("Details"));
    expect(screen.getByText("settings.updated")).toBeVisible();
    expect(screen.getByText("api key changed").nextSibling).toHaveTextContent("no");
    expect(screen.getByText(/"to": "\/Bin"/)).toBeVisible();
  });

  it("renders events as a table on desktop, with tokens for analyses", async () => {
    setMatchMedia(true);
    vi.mocked(apiClient.getAudit).mockResolvedValue(
      page([
        event({
          id: "evt-2",
          actor_label: "system",
          action: "document.analyzed",
          summary: "Analysed scan.pdf",
          prompt_tokens: 3950,
          completion_tokens: 670,
          total_tokens: 4620,
        }),
      ]),
    );
    renderPage();

    const table = await screen.findByRole("table");
    const row = within(table).getByText("Analysed scan.pdf").closest("tr");
    expect(row).toHaveTextContent("system");
    expect(row).toHaveTextContent("4.6K");
  });

  it("refetches with the category when a filter is chosen", async () => {
    vi.mocked(apiClient.getAudit).mockResolvedValue(page([event()]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Changed the trash folder");
    expect(apiClient.getAudit).toHaveBeenLastCalledWith(50, undefined, undefined);

    vi.mocked(apiClient.getAudit).mockResolvedValue(
      page([event({ id: "evt-3", action: "auth.login", summary: "Signed in" })]),
    );
    const signIns = screen.getByRole("button", { name: "Sign-ins" });
    await user.click(signIns);

    expect(await screen.findByText("Signed in")).toBeInTheDocument();
    expect(apiClient.getAudit).toHaveBeenLastCalledWith(50, undefined, "auth");
    expect(signIns).toHaveAttribute("aria-pressed", "true");
  });

  it("loads the next page on Load more, keeping the first", async () => {
    vi.mocked(apiClient.getAudit).mockResolvedValueOnce(page([event()], "cursor-2"));
    const user = userEvent.setup();
    renderPage();

    const loadMore = await screen.findByRole("button", { name: /load more/i });
    vi.mocked(apiClient.getAudit).mockResolvedValueOnce(
      page([event({ id: "evt-4", summary: "Signed out" })]),
    );
    await user.click(loadMore);

    expect(await screen.findByText("Signed out")).toBeInTheDocument();
    expect(screen.getByText("Changed the trash folder")).toBeInTheDocument();
    expect(apiClient.getAudit).toHaveBeenLastCalledWith(50, "cursor-2", undefined);
  });

  it("shows a non-admin their own activity too", async () => {
    vi.mocked(apiClient.getCurrentUser).mockResolvedValue({ ...ADMIN, is_admin: false });
    renderPage();

    await waitFor(() => expect(apiClient.getAudit).toHaveBeenCalled());
    expect(apiClient.getAuditUsage).toHaveBeenCalled();
  });
});
