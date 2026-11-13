import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AIProposal, Document } from "@/services/api/types";
import { ProducedBy } from "./ProducedBy";

const PROPOSAL: AIProposal = {
  suggested_name: "2026.05.18 Swisscom Rechnung",
  fields: null,
  target_folder_path: "/Documents/Finance",
  document_date: "2026-05-18",
  confidence_score: 0.9,
  reasoning_text: "Invoice header.",
  model_name: "gpt-4o",
  connection_name: "Infomaniak",
  configuration_name: "Daily",
  reading_label: "the text layer",
  prompt_text: null,
  system_prompt: "You file scanned documents.",
};

const DOCUMENT: Document = {
  id: "1",
  original_filename: "scan_1.pdf",
  extension: ".pdf",
  mime_type: "application/pdf",
  file_size_bytes: 1200,
  page_count: 1,
  scanned_at: "2026-05-18T10:00:00Z",
  status: "pending",
  skip_count: 0,
  proposal_status: "ready",
  analysis_stage: null,
  proposal: PROPOSAL,
  proposal_error: null,
  ocr_status: "not_needed",
  ocr_error: null,
  analysis_usage: null,
  waiting_reason: null,
  configuration_name: null,
  reading: null,
};

function renderBlock(document: Document) {
  const handlers = { onReanalyse: vi.fn(), onReanalyseWith: vi.fn(), onCompare: vi.fn() };
  render(<ProducedBy document={document} isReanalysing={false} {...handlers} />);
  return { block: screen.getByRole("region", { name: "Produced by" }), ...handlers };
}

describe("ProducedBy", () => {
  it("names the configuration, the reading and the model, with the reasoning inside", async () => {
    const { block, onReanalyse, onReanalyseWith } = renderBlock(DOCUMENT);

    expect(block).toHaveTextContent("Configuration “Daily” · read with the text layer");
    expect(block).toHaveTextContent("Infomaniak · gpt-4o");
    expect(block).toHaveTextContent("Invoice header.");
    const user = userEvent.setup();
    await user.click(within(block).getByRole("button", { name: "Re-analyse" }));
    await user.click(within(block).getByRole("button", { name: "Re-analyse with…" }));
    expect(onReanalyse).toHaveBeenCalledOnce();
    expect(onReanalyseWith).toHaveBeenCalledOnce();
  });

  it("says nothing about a configuration for a proposal from before them", () => {
    const { block } = renderBlock({
      ...DOCUMENT,
      proposal: { ...PROPOSAL, configuration_name: null, reading_label: null },
    });

    expect(block).not.toHaveTextContent("Configuration “");
  });

  it("reveals what was sent to the AI only once asked, and hides it again on request", async () => {
    const user = userEvent.setup();
    renderBlock({ ...DOCUMENT, proposal: { ...PROPOSAL, prompt_text: "Folders available:" } });

    expect(screen.queryByText("You file scanned documents.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /show what was sent to the ai/i }));
    expect(screen.getByText("You file scanned documents.")).toBeInTheDocument();
    expect(screen.getByText(/Folders available:/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /hide what was sent to the ai/i }));
    expect(screen.queryByText("You file scanned documents.")).not.toBeInTheDocument();
  });

  it("explains when the document-specific prompt wasn't recorded for an older proposal", async () => {
    const user = userEvent.setup();
    renderBlock(DOCUMENT);

    await user.click(screen.getByRole("button", { name: /show what was sent to the ai/i }));

    expect(screen.getByText(/wasn't recorded for this proposal/i)).toBeInTheDocument();
  });

  it("offers Try again and shows what a failed analysis cost", () => {
    const { block } = renderBlock({
      ...DOCUMENT,
      proposal_status: "failed",
      proposal: null,
      proposal_error: "The model chose '/Elsewhere', which is outside your folders.",
      analysis_usage: {
        extraction: null,
        filing: {
          model_label: "Infomaniak · gpt-4o",
          prompt_tokens: 1150,
          completion_tokens: 58,
          total_tokens: 1208,
          calls: 1,
          unreported_calls: 0,
        },
        total_tokens: 1208,
      },
    });

    expect(within(block).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(within(block).getByText("Tokens").closest("summary")).toHaveTextContent(
      "Tokens · filing 1.2K · total 1.2K",
    );
  });
});
