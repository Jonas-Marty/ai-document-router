import { zodResolver } from "@hookform/resolvers/zod";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FormProvider, useForm } from "react-hook-form";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useFolderContext } from "@/hooks/useFolders";
import type { Document, FolderContext, Settings } from "@/services/api/types";
import { ReviewForm } from "./ReviewForm";
import { formValuesFor, type ReviewFormValues, reviewFormSchema } from "./reviewFormSchema";

vi.mock("@/services/api/client", () => ({
  apiClient: {
    getFolderContext: vi.fn(),
    getSettings: vi.fn(),
    getKnowledge: vi.fn(),
    listKnownSenders: vi.fn(),
  },
}));

import { apiClient } from "@/services/api/client";

const baseDocument: Document = {
  id: "doc-1",
  original_filename: "invoice.pdf",
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
    suggested_name: "2026.05.18 Test Invoice",
    fields: null,
    target_folder_path: "/Documents/Finance",
    document_date: "2026-05-18",
    confidence_score: 0.92,
    reasoning_text: "Looks like an invoice.",
    model_name: "test-model",
    connection_name: null,
    configuration_name: null,
    reading_label: null,
    prompt_text: "Folders available (choose exactly one):\n/Documents/Finance",
    system_prompt: "You file scanned documents.",
  },
  proposal_error: null,
  ocr_status: "not_needed",
  ocr_error: null,
  analysis_usage: null,
  waiting_reason: null,
  configuration_name: null,
  reading: null,
};

const emptySettings: Settings = {
  allowed_root_folders: ["/Documents"],
  trash_folder_path: "/Trash",
  watch_folder_path: "/Scans/Inbox",
  filename_pattern: null,
  filename_pattern_hint: null,
  store_ocr_text: true,
  proposal_batch_size: 10,
};

function folderContextOf(overrides: Partial<FolderContext> = {}): FolderContext {
  return {
    path: "/Documents/Finance",
    exists: true,
    siblings: [],
    total_file_count: 0,
    filename_collision: false,
    ...overrides,
  };
}

function Harness({
  document,
  onChooseFolder = () => {},
}: {
  document: Document;
  onChooseFolder?: () => void;
}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const methods = useForm<ReviewFormValues>({
    resolver: zodResolver(reviewFormSchema),
    mode: "onChange",
    defaultValues: formValuesFor(document.proposal),
  });
  return (
    <QueryClientProvider client={queryClient}>
      <FormProvider {...methods}>
        <ConnectedReviewForm document={document} onChooseFolder={onChooseFolder} />
      </FormProvider>
    </QueryClientProvider>
  );
}

function ConnectedReviewForm({
  document,
  onChooseFolder,
}: {
  document: Document;
  onChooseFolder: () => void;
}) {
  const folderContext = useFolderContext(
    document.proposal?.target_folder_path ?? "",
    document.proposal ? `${document.proposal.suggested_name}${document.extension}` : "",
  );
  return (
    <ReviewForm document={document} folderContext={folderContext} onChooseFolder={onChooseFolder} />
  );
}

describe("ReviewForm", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("shows the confidence badge and prefilled fields for a ready proposal", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    render(<Harness document={baseDocument} />);

    expect(screen.getByText(/high confidence/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026.05.18 Test Invoice")).toBeInTheDocument();
    expect(screen.getByText(".pdf")).toBeInTheDocument();
    expect(screen.getByText("/Documents/Finance")).toBeInTheDocument();
  });

  it("names the connection and model, and the configuration and reading behind them", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const proposal = {
      ...baseDocument.proposal,
      connection_name: "Infomaniak",
      configuration_name: "Careful",
      reading_label: "Text layer",
    };
    render(<Harness document={{ ...baseDocument, proposal } as Document} />);

    expect(screen.getByText("Infomaniak · test-model")).toBeInTheDocument();
    expect(screen.getByText("Configuration “Careful” · read with Text layer")).toBeInTheDocument();
  });

  it("says nothing about a configuration for a proposal from before them", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    render(<Harness document={baseDocument} />);

    expect(screen.queryByText(/Configuration “/)).not.toBeInTheDocument();
  });

  it("reveals what was sent to the AI only once asked, and hides it again on request", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const user = userEvent.setup();
    render(<Harness document={baseDocument} />);

    expect(screen.queryByText("You file scanned documents.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /show what was sent to the ai/i }));
    expect(screen.getByText("You file scanned documents.")).toBeInTheDocument();
    expect(screen.getByText(/Folders available \(choose exactly one\):/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /hide what was sent to the ai/i }));
    expect(screen.queryByText("You file scanned documents.")).not.toBeInTheDocument();
  });

  it("shows the text filing read, and what read it, only once asked", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const user = userEvent.setup();
    render(
      <Harness
        document={{
          ...baseDocument,
          reading: { label: "Jonas-PC · chandra-ocr-2", text: "# Swisscom\nRechnung Mai" },
        }}
      />,
    );

    expect(screen.queryByText(/Rechnung Mai/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /show the text filing read/i }));
    expect(screen.getByText(/Rechnung Mai/)).toBeInTheDocument();
    expect(screen.getByText("Read by Jonas-PC · chandra-ocr-2")).toBeInTheDocument();
  });

  it("offers no reading when there is none", () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    render(<Harness document={baseDocument} />);

    expect(screen.queryByRole("button", { name: /text filing read/i })).not.toBeInTheDocument();
  });

  it("explains when the document-specific prompt wasn't recorded for an older proposal", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const user = userEvent.setup();
    render(
      <Harness
        document={{
          ...baseDocument,
          proposal: {
            suggested_name: "2026.05.18 Test Invoice",
            fields: null,
            target_folder_path: "/Documents/Finance",
            document_date: "2026-05-18",
            confidence_score: 0.92,
            reasoning_text: "Looks like an invoice.",
            model_name: "test-model",
            connection_name: null,
            configuration_name: null,
            reading_label: null,
            prompt_text: null,
            system_prompt: "You file scanned documents.",
          },
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: /show what was sent to the ai/i }));

    expect(screen.getByText(/wasn't recorded for this proposal/i)).toBeInTheDocument();
  });

  it("shows the SPEC 7.4 low-confidence banner below the 0.60 threshold", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const lowConfidence: Document = {
      ...baseDocument,
      proposal: {
        suggested_name: "2026.05.18 Test Invoice",
        fields: null,
        target_folder_path: "/Documents/Finance",
        document_date: "2026-05-18",
        confidence_score: 0.4,
        reasoning_text: "Looks like an invoice.",
        model_name: "test-model",
        connection_name: null,
        configuration_name: null,
        reading_label: null,
        prompt_text: "Folders available (choose exactly one):\n/Documents/Finance",
        system_prompt: "You file scanned documents.",
      },
    };
    render(<Harness document={lowConfidence} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /low confidence — check the folder and date/i,
    );
  });

  it("shows proposal_error instead of a confidence badge when the proposal failed", () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const failed: Document = {
      ...baseDocument,
      proposal_status: "failed",
      proposal: null,
      proposal_error: "The AI endpoint timed out.",
    };
    render(<Harness document={failed} />);

    expect(screen.getByText("The AI endpoint timed out.")).toBeInTheDocument();
    expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
    // SPEC 8.8: "full manual approvability" -- fields are present and editable even without
    // a proposal.
    expect(screen.getByLabelText(/file name/i)).toBeEnabled();
  });

  it("shows what a failed analysis cost, since it spent tokens without a proposal", () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const failed: Document = {
      ...baseDocument,
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
    };
    render(<Harness document={failed} />);

    expect(screen.getByText("Tokens").closest("summary")).toHaveTextContent(
      "Tokens · filing 1.2K · total 1.2K",
    );
  });

  it("says a searchable copy will be filed when one is ready, and stays quiet otherwise", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const { unmount } = render(<Harness document={baseDocument} />);
    expect(screen.queryByText(/searchable copy/i)).not.toBeInTheDocument();
    unmount();

    render(<Harness document={{ ...baseDocument, ocr_status: "ready" }} />);
    expect(await screen.findByText(/a searchable copy is filed in its place/i)).toBeInTheDocument();
  });

  it("says nothing about a searchable copy when the setting is off", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue({
      ...emptySettings,
      store_ocr_text: false,
    });
    render(<Harness document={{ ...baseDocument, ocr_status: "ready" }} />);

    // The copy exists -- it was made before the setting was turned off -- but approve will
    // not upload it, so promising the person a searchable file would be a lie.
    await screen.findByLabelText(/file name/i);
    expect(screen.queryByText(/a searchable copy is filed in its place/i)).not.toBeInTheDocument();
  });

  it("explains an OCR failure and that the document is filed as it is", () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    render(
      <Harness
        document={{
          ...baseDocument,
          ocr_status: "failed",
          ocr_error: "This PDF is encrypted, so it can't be OCR'd.",
        }}
      />,
    );

    expect(screen.getByText(/This PDF is encrypted, so it can't be OCR'd\./)).toBeInTheDocument();
  });

  describe("name fields (SPEC 7.1a)", () => {
    const fieldDocument: Document = {
      ...baseDocument,
      proposal: baseDocument.proposal && {
        ...baseDocument.proposal,
        suggested_name: "2026.05.18 Swisscom Rechnung",
        fields: {
          sender: "Swisscom",
          account: null,
          title: "Rechnung",
          period: null,
          number: null,
        },
      },
    };

    it("prefills each field and shows the assembled name as it is typed", async () => {
      vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
      vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
      const user = userEvent.setup();
      render(<Harness document={fieldDocument} />);

      expect(screen.getByLabelText("Sender")).toHaveValue("Swisscom");
      expect(screen.getByLabelText("Title")).toHaveValue("Rechnung");
      const preview = screen.getByLabelText("Assembled file name");
      expect(preview).toHaveTextContent("2026.05.18 Swisscom Rechnung");

      await user.type(screen.getByLabelText(/Number/), "9291491");
      expect(preview).toHaveTextContent("2026.05.18 Swisscom Rechnung 9291491");
    });

    it("says which required field is missing", async () => {
      vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
      vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
      const user = userEvent.setup();
      render(<Harness document={fieldDocument} />);

      await user.clear(screen.getByLabelText("Sender"));

      expect(await screen.findByText("Sender is required.")).toBeInTheDocument();
    });

    it("hands the assembled name over to an editable one, and gives the fields back", async () => {
      vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
      vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
      const user = userEvent.setup();
      render(<Harness document={fieldDocument} />);

      await user.click(screen.getByLabelText("Write the name by hand"));

      expect(screen.queryByLabelText("Sender")).not.toBeInTheDocument();
      expect(screen.getByLabelText(/file name/i)).toHaveValue("2026.05.18 Swisscom Rechnung");

      await user.click(screen.getByLabelText("Write the name by hand"));
      expect(screen.getByLabelText("Sender")).toHaveValue("Swisscom");
    });

    it("asks to remember a new sender and account, unticked, and not what is known", async () => {
      vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
      vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
      vi.mocked(apiClient.listKnownSenders).mockResolvedValue([
        { id: "s1", short_name: "Swisscom", aliases: [], accounts: [] },
      ]);
      const user = userEvent.setup();
      render(<Harness document={fieldDocument} />);

      // A known sender with no account: nothing to ask yet.
      await waitFor(() => expect(apiClient.listKnownSenders).toHaveBeenCalled());
      expect(screen.queryByText("Remember for next time")).not.toBeInTheDocument();

      await user.type(screen.getByLabelText(/Account/), "CH33 0070");
      const account = await screen.findByLabelText("Remember CH33 0070 for Swisscom");
      expect(account).not.toBeChecked();
      expect(screen.queryByLabelText(/as a sender/)).not.toBeInTheDocument();

      await user.clear(screen.getByLabelText("Sender"));
      await user.type(screen.getByLabelText("Sender"), "Salt");
      expect(screen.getByLabelText("Remember “Salt” as a sender")).not.toBeChecked();
    });

    it("orders the fields and the name as the owner's template says", async () => {
      vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
      vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
      vi.mocked(apiClient.getKnowledge).mockResolvedValue({
        name_template: ["sender", "title", "date"],
        filing_rules: null,
      });
      render(<Harness document={fieldDocument} />);

      const preview = screen.getByLabelText("Assembled file name");
      await waitFor(() => expect(preview).toHaveTextContent("Swisscom Rechnung 2026.05.18"));
      const labels = screen.getAllByRole("textbox").map((input) => input.id);
      expect(labels).toEqual(["review-sender", "review-title"]);
    });
  });

  it("opens a proposal from before name fields on its whole name, hand-written", () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    render(<Harness document={baseDocument} />);

    expect(screen.getByLabelText("Write the name by hand")).toBeChecked();
    expect(screen.queryByLabelText("Sender")).not.toBeInTheDocument();
  });

  it("shows a blocking error for a forbidden character as the user types (mode: onChange)", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const user = userEvent.setup();
    render(<Harness document={baseDocument} />);

    const nameInput = screen.getByLabelText(/file name/i);
    await user.clear(nameInput);
    await user.type(nameInput, "bad/name");

    expect(await screen.findByText(/can't contain \//)).toBeInTheDocument();
  });

  it("shows the blocking collision message when the folder context reports one", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(
      folderContextOf({ filename_collision: true }),
    );
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    render(<Harness document={baseDocument} />);

    expect(await screen.findByText(/already exists in this folder/i)).toBeInTheDocument();
  });

  it("shows a non-blocking pattern-mismatch warning without disabling anything", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue({
      ...emptySettings,
      filename_pattern: "^\\d{4}\\.\\d{2}\\.\\d{2} ",
      filename_pattern_hint: "Expected YYYY.MM.DD prefix",
    });
    const user = userEvent.setup();
    render(<Harness document={baseDocument} />);

    const nameInput = screen.getByLabelText(/file name/i);
    await user.clear(nameInput);
    await user.type(nameInput, "no date prefix");

    await waitFor(() => {
      expect(screen.getByText(/doesn't match your naming pattern/)).toBeInTheDocument();
    });
    // The hint itself waits behind a click: it can be the whole naming guide.
    expect(screen.getByText("Expected YYYY.MM.DD prefix")).not.toBeVisible();
    await user.click(screen.getByText(/show the pattern/i));
    expect(screen.getByText("Expected YYYY.MM.DD prefix")).toBeVisible();
    expect(nameInput).toBeEnabled();
  });

  it("renders siblings from the shared folder context query, not a second fetch", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(
      folderContextOf({
        siblings: [{ filename: "2026.01.01 Old Invoice.pdf", created_at: null, size_bytes: 100 }],
      }),
    );
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    render(<Harness document={baseDocument} />);

    expect(await screen.findByText("2026.01.01 Old Invoice.pdf")).toBeInTheDocument();
    expect(apiClient.getFolderContext).toHaveBeenCalledTimes(1);
  });
});
