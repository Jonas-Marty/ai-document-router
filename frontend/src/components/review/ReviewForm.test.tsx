import { zodResolver } from "@hookform/resolvers/zod";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FormProvider, useForm } from "react-hook-form";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useFolderContext } from "@/hooks/useFolders";
import type { Document, FolderContext, Settings } from "@/services/api/types";
import { ReviewForm } from "./ReviewForm";
import { type ReviewFormValues, reviewFormSchema } from "./reviewFormSchema";

vi.mock("@/services/api/client", () => ({
  apiClient: { getFolderContext: vi.fn(), getSettings: vi.fn() },
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
  proposal: {
    suggested_name: "2026.05.18 Test Invoice",
    target_folder_path: "/Documents/Finance",
    document_date: "2026-05-18",
    confidence_score: 0.92,
    reasoning_text: "Looks like an invoice.",
    model_name: "test-model",
  },
  proposal_error: null,
};

const emptySettings: Settings = {
  allowed_root_folders: ["/Documents"],
  trash_folder_path: "/Trash",
  filename_pattern: null,
  filename_pattern_hint: null,
  ai_endpoint_url: "http://localhost:11434",
  ai_model_name: "test-model",
  ai_api_key_set: false,
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
    defaultValues: {
      documentDate: document.proposal?.document_date ?? "",
      name: document.proposal?.suggested_name ?? "",
      folderPath: document.proposal?.target_folder_path ?? "",
    },
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

  it("shows the SPEC 7.4 low-confidence banner below the 0.60 threshold", async () => {
    vi.mocked(apiClient.getFolderContext).mockResolvedValue(folderContextOf());
    vi.mocked(apiClient.getSettings).mockResolvedValue(emptySettings);
    const lowConfidence: Document = {
      ...baseDocument,
      proposal: {
        suggested_name: "2026.05.18 Test Invoice",
        target_folder_path: "/Documents/Finance",
        document_date: "2026-05-18",
        confidence_score: 0.4,
        reasoning_text: "Looks like an invoice.",
        model_name: "test-model",
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
      expect(screen.getByText("Expected YYYY.MM.DD prefix")).toBeInTheDocument();
    });
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
