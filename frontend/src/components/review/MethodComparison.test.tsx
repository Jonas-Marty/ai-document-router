import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { MethodResult } from "@/services/api/types";
import { MethodComparison } from "./MethodComparison";

function result(overrides: Partial<MethodResult> = {}): MethodResult {
  return {
    method: "text_layer",
    model_name: "text-model",
    label: "Text layer",
    text_preview: "Helvetia Versicherungspolice",
    proposal: {
      suggested_name: "2026.04.16 Helvetia Police",
      target_folder_path: "/Documents/Insurance",
      document_date: "2026-04-16",
      confidence_score: 0.88,
      reasoning_text: "Letterhead reads Helvetia.",
      model_name: "text-model",
      prompt_text: null,
      system_prompt: "You file scanned documents.",
    },
    error: null,
    duration_ms: 4231,
    ...overrides,
  };
}

function renderComparison(props: Partial<Parameters<typeof MethodComparison>[0]> = {}) {
  const onUse = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <MethodComparison
      open
      onOpenChange={onOpenChange}
      extension=".pdf"
      results={[result()]}
      isPending={false}
      error={null}
      onUse={onUse}
      {...props}
    />,
  );
  return { onUse, onOpenChange };
}

describe("MethodComparison", () => {
  it("shows what each method proposed, with the extension the file will actually get", () => {
    renderComparison({
      results: [
        result(),
        result({
          method: "markdown",
          model_name: "qwen2.5vl:7b",
          label: "Markdown · Local · qwen2.5vl:7b",
          proposal: {
            suggested_name: "2026.04.16 Helvetia Versicherungspolice",
            target_folder_path: "/Documents/Insurance",
            document_date: "2026-04-16",
            confidence_score: 0.95,
            reasoning_text: "Reads the letterhead directly.",
            model_name: "qwen2.5vl:7b",
            prompt_text: null,
            system_prompt: "You file scanned documents.",
          },
        }),
      ],
    });

    expect(screen.getByText("Text layer")).toBeInTheDocument();
    expect(screen.getByText("Markdown · Local · qwen2.5vl:7b")).toBeInTheDocument();
    expect(screen.getByText("2026.04.16 Helvetia Police.pdf")).toBeInTheDocument();
    expect(screen.getByText("2026.04.16 Helvetia Versicherungspolice.pdf")).toBeInTheDocument();
  });

  it("reports a method that produced nothing, rather than hiding it", () => {
    // "Tesseract isn't installed" is the finding someone comparing methods needs.
    renderComparison({
      results: [
        result({
          method: "ocr",
          label: "Tesseract OCR",
          proposal: null,
          error: "Tesseract isn't installed in this image.",
          duration_ms: 0,
        }),
      ],
    });

    expect(screen.getByText("Tesseract OCR")).toBeInTheDocument();
    expect(screen.getByText("Tesseract isn't installed in this image.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /use this/i })).not.toBeInTheDocument();
  });

  it("reports how long a method took, in seconds", () => {
    // The question being answered is "is this worth waiting for", which 4.2s answers and
    // 4231ms makes you do arithmetic for.
    renderComparison();

    expect(screen.getByText("4.2s")).toBeInTheDocument();
  });

  it("hands back the chosen method and closes", async () => {
    const { onUse, onOpenChange } = renderComparison();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /use this/i }));

    expect(onUse).toHaveBeenCalledWith(expect.objectContaining({ label: "Text layer" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("says it is working rather than showing an empty list", () => {
    renderComparison({ isPending: true, results: undefined });

    expect(screen.getByText(/one model call per method/i)).toBeInTheDocument();
  });

  it("shows the backend's reason when the comparison itself fails", () => {
    renderComparison({ results: undefined, error: "Couldn't read the file." });

    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't read the file.");
  });
});
