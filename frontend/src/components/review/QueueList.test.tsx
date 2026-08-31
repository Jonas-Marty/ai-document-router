import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AIProposal, Document } from "@/services/api/types";
import { QueueList } from "./QueueList";

function proposal(overrides: Partial<AIProposal> = {}): AIProposal {
  return {
    suggested_name: "2026.05.18 Swisscom Rechnung",
    target_folder_path: "/Documents/Finance",
    document_date: "2026-05-18",
    confidence_score: 0.9,
    reasoning_text: "Invoice header.",
    model_name: "test-model",
    ...overrides,
  };
}

function doc(id: string, overrides: Partial<Document> = {}): Document {
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
    proposal: proposal(),
    proposal_error: null,
    ...overrides,
  };
}

function renderList(items: Document[], totalPending = items.length, currentId = items[0]?.id) {
  const onSelect = vi.fn();
  const onRetryFailed = vi.fn();
  render(
    <QueueList
      items={items}
      totalPending={totalPending}
      currentId={currentId}
      onSelect={onSelect}
      onRetryFailed={onRetryFailed}
      isRetryingFailed={false}
    />,
  );
  return { onSelect, onRetryFailed };
}

describe("QueueList", () => {
  it("names each row by what the document will be called, not by the scanner's filename", () => {
    // Every scan is "scan_0041.pdf", so the original filename is the one thing that cannot
    // tell two rows apart.
    renderList([
      doc("1", { proposal: proposal({ suggested_name: "2026.05.18 Swisscom Rechnung" }) }),
      doc("2", { proposal: proposal({ suggested_name: "2026.05.19 SAC Spendenbescheinigung" }) }),
    ]);

    expect(screen.getByText("2026.05.18 Swisscom Rechnung.pdf")).toBeInTheDocument();
    expect(screen.getByText("2026.05.19 SAC Spendenbescheinigung.pdf")).toBeInTheDocument();
    expect(screen.queryByText("scan_1.pdf")).not.toBeInTheDocument();
  });

  it("falls back to the original filename while there is no proposal yet", () => {
    renderList([doc("1", { proposal_status: "pending", proposal: null })]);

    expect(screen.getByText("scan_1.pdf")).toBeInTheDocument();
    expect(screen.getByText(/waiting for the ai proposal/i)).toBeInTheDocument();
  });

  it("shows why a document is not ready, in the review form's own words", () => {
    renderList([
      doc("1", {
        proposal_status: "failed",
        proposal: null,
        proposal_error: "No text layer found — OCR isn't set up yet.",
      }),
    ]);

    expect(screen.getByText("No text layer found — OCR isn't set up yet.")).toBeInTheDocument();
  });

  it("marks the document being reviewed and flags skipped ones", () => {
    renderList([doc("1"), doc("2", { status: "skipped", skip_count: 2 })], 2, "1");

    const rows = screen.getAllByRole("button");
    expect(rows[0]).toHaveAttribute("aria-current", "true");
    expect(rows[1]).not.toHaveAttribute("aria-current");
    expect(screen.getByText("Reviewing")).toBeInTheDocument();
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });

  it("reports the documents behind the ones /queue returned", () => {
    // The endpoint is capped at QUEUE_LIMIT, so on a backlog the list is a window onto the
    // front of the queue and the header count would otherwise not add up to the rows.
    renderList([doc("1"), doc("2")], 37);

    expect(screen.getByText(/35 more behind these/)).toBeInTheDocument();
  });

  it("says nothing about a backlog when every queued document is listed", () => {
    renderList([doc("1"), doc("2")], 2);

    expect(screen.queryByText(/more behind these/)).not.toBeInTheDocument();
  });

  it("offers a bulk retry when something in the queue has failed", async () => {
    // The poller never revisits a failed proposal, so after a configuration fix the only
    // route back would be opening all of them and pressing Try again one at a time.
    const { onRetryFailed } = renderList([
      doc("1"),
      doc("2", { proposal_status: "failed", proposal: null, proposal_error: "No folders." }),
    ]);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /retry failed/i }));

    expect(onRetryFailed).toHaveBeenCalled();
  });

  it("offers no bulk retry when nothing has failed", () => {
    renderList([doc("1"), doc("2")]);

    expect(screen.queryByRole("button", { name: /retry failed/i })).not.toBeInTheDocument();
  });

  it("hands back the document that was picked", async () => {
    const { onSelect } = renderList(
      [doc("1"), doc("2", { proposal: proposal({ suggested_name: "2026.05.19 SAC Spende" }) })],
      2,
      "1",
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /2026.05.19 SAC Spende/ }));

    expect(onSelect).toHaveBeenCalledWith("2");
  });
});
