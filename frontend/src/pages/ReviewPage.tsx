import { zodResolver } from "@hookform/resolvers/zod";
import { ListChecks } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { FolderPicker } from "@/components/folders/FolderPicker";
import { ActionBar } from "@/components/review/ActionBar";
import type { DesktopDocumentPaneHandle } from "@/components/review/DesktopDocumentPane";
import { DesktopDocumentPane } from "@/components/review/DesktopDocumentPane";
import { DocumentViewer } from "@/components/review/DocumentViewer";
import { MethodComparison } from "@/components/review/MethodComparison";
import { QueuePanel } from "@/components/review/QueuePanel";
import { RerunDialog } from "@/components/review/RerunDialog";
import { ResizableSplit } from "@/components/review/ResizableSplit";
import { ReviewForm } from "@/components/review/ReviewForm";
import {
  approveRequestFor,
  effectiveName,
  formValuesFor,
  makeReviewFormSchema,
  type ReviewFormValues,
  rememberPrompts,
} from "@/components/review/reviewFormSchema";
import { ShortcutCheatSheet } from "@/components/review/ShortcutCheatSheet";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useIsDesktop } from "@/hooks/useBreakpoint";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import {
  documentContentUrl,
  useApproveDocument,
  useCompareDocument,
  useRegenerateDocument,
  useRetryFailedProposals,
  useSkipDocument,
  useTrashDocument,
} from "@/hooks/useDocument";
import { useFolderContext } from "@/hooks/useFolders";
import { useKnowledge, useKnownSenders } from "@/hooks/useKnowledge";
import { useReviewQueue } from "@/hooks/useReviewQueue";
import { useReviewShortcuts } from "@/hooks/useReviewShortcuts";
import { ApiError } from "@/services/api/errors";
import type { Document } from "@/services/api/types";

export default function ReviewPage() {
  const { id: requestedId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queue = useReviewQueue(requestedId);
  const currentId = queue.currentDocument?.id;

  // SPEC 8.8: the URL follows the current document, so a reload or a shared link lands on
  // it. Keyed on the document alone: the URL changing is the hook's input, not this one's.
  // biome-ignore lint/correctness/useExhaustiveDependencies: follows the document only
  useEffect(() => {
    if (queue.isLoading || !currentId || currentId === requestedId) return;
    navigate(`/review/${currentId}`, { replace: true });
  }, [currentId, queue.isLoading]);
  const [queueOpen, setQueueOpen] = useState(false);
  const retryFailed = useRetryFailedProposals();

  function handleRetryFailed() {
    retryFailed.mutate(undefined, {
      // The count comes from the server, not from the visible rows: /queue is capped, so a
      // backlog can hold failures this list never showed.
      onSuccess: ({ retried }) =>
        toast.success(
          retried === 1
            ? "Asked for 1 proposal again. It appears as the poller gets to it."
            : `Asked for ${retried} proposals again. They appear as the poller gets to them.`,
        ),
      onError: (error) =>
        toast.error(
          error instanceof ApiError ? error.message : "Couldn't retry the failed proposals.",
        ),
    });
  }

  return (
    <div className="mx-auto flex h-full max-w-6xl flex-col lg:max-w-none">
      <div className="flex items-center justify-between gap-2 p-4 pb-0">
        <h1 className="text-lg font-semibold">Review</h1>
        {/* The count is the whole point of the control, so it is in the label rather than
            behind the click: "what is still open?" is answered without opening anything. */}
        {queue.totalPending > 0 && (
          <Button variant="outline" size="sm" onClick={() => setQueueOpen(true)}>
            <ListChecks className="size-4" aria-hidden="true" />
            Queue
            <span className="rounded bg-secondary px-1.5 py-0.5 text-xs font-medium tabular-nums text-secondary-foreground">
              {queue.totalPending}
            </span>
          </Button>
        )}
      </div>
      {queue.isLoading ? (
        <QueueSkeleton />
      ) : queue.isError ? (
        <div className="p-4">
          <ErrorState
            message={
              queue.error instanceof ApiError ? queue.error.message : "Couldn't load the queue."
            }
            onRetry={() => queue.refetch()}
          />
        </div>
      ) : !queue.currentDocument ? (
        <div className="p-4">
          <EmptyState
            title="Queue's clear."
            description="New scans appear in the Inbox automatically."
            action={{ label: "Back to the Inbox", onClick: () => navigate("/") }}
          />
        </div>
      ) : (
        <DocumentReview
          key={queue.currentDocument.id}
          document={queue.currentDocument}
          nextDocument={queue.items.find((d) => d.id !== queue.currentDocument?.id)}
          advancePast={queue.advancePast}
        />
      )}
      <QueuePanel
        open={queueOpen}
        onOpenChange={setQueueOpen}
        items={queue.items}
        totalPending={queue.totalPending}
        currentId={queue.currentDocument?.id}
        onSelect={queue.selectDocument}
        onRetryFailed={handleRetryFailed}
        isRetryingFailed={retryFailed.isPending}
      />
    </div>
  );
}

function QueueSkeleton() {
  return (
    <div className="space-y-4 p-4" aria-busy="true">
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-8 w-1/3" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
    </div>
  );
}

/** Everything for one document: the form instance, the debounced folder-context query (SPEC
 * 8.3: shared between the sibling list and the blocking collision check), the mutations, and
 * -- desktop only -- the resizable split and keyboard shortcuts. Keyed by document id from
 * the parent so switching documents gets a clean useForm/useState lifecycle rather than
 * fighting stale state across a shared instance. */
function DocumentReview({
  document,
  nextDocument,
  advancePast,
}: {
  document: Document;
  nextDocument: Document | undefined;
  advancePast: (id: string) => void;
}) {
  const isDesktop = useIsDesktop();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [cheatSheetOpen, setCheatSheetOpen] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);
  const viewerRef = useRef<DesktopDocumentPaneHandle>(null);

  const template = useKnowledge().data?.name_template;
  const knownSenders = useKnownSenders().data;
  const schema = useMemo(() => makeReviewFormSchema(template), [template]);
  // useForm keeps the resolver it was first given; the template arrives after the first
  // render, so the resolver reads the current schema through a ref.
  const schemaRef = useRef(schema);
  schemaRef.current = schema;
  const methods = useForm<ReviewFormValues>({
    resolver: (values, context, options) =>
      zodResolver(schemaRef.current)(values, context, options),
    // ActionBar disables Approve on formState.isValid with no separate submit step -- see
    // reviewFormSchema.ts for why this must be "onChange".
    mode: "onChange",
    defaultValues: formValuesFor(document.proposal),
  });

  // Regenerate can turn a "failed" proposal into a "ready" one for the *same* document (same
  // key), so the reset also has to watch proposal_status, not just re-run on mount.
  // biome-ignore lint/correctness/useExhaustiveDependencies: methods.reset is stable (RHF)
  useEffect(() => {
    methods.reset(formValuesFor(document.proposal));
  }, [document.proposal_status]);

  const values = methods.watch();
  const folderPath = values.folderPath;
  const name = effectiveName(values, template);
  const debouncedFolderPath = useDebouncedValue(folderPath, 300);
  const debouncedFilename = useDebouncedValue(`${name}${document.extension}`, 300);
  const folderContext = useFolderContext(debouncedFolderPath, debouncedFilename);

  // SPEC 8.8: prefetch the next document's content in the background.
  useEffect(() => {
    if (!nextDocument) return;
    const controller = new AbortController();
    fetch(documentContentUrl(nextDocument.id), { signal: controller.signal }).catch(() => {});
    return () => controller.abort();
  }, [nextDocument]);

  const approveMutation = useApproveDocument();
  const skipMutation = useSkipDocument();
  const trashMutation = useTrashDocument();
  const regenerateMutation = useRegenerateDocument();
  const [rerunOpen, setRerunOpen] = useState(false);
  const compareMutation = useCompareDocument();

  function openComparison() {
    setCompareOpen(true);
    // Reopening shows the last run rather than paying for another one; "Run again" inside
    // the overlay is there for when the configuration changed since.
    if (!compareMutation.data && !compareMutation.isPending) {
      compareMutation.mutate(document.id);
    }
  }

  function handleApprove(values: ReviewFormValues) {
    setApproveError(null);
    approveMutation.mutate(
      {
        id: document.id,
        body: approveRequestFor(values, rememberPrompts(values, knownSenders)),
      },
      {
        onSuccess: (response) => {
          toast.success(`Moved to ${response.history_entry.final_folder_path}`);
        },
        onError: (error) => {
          const message =
            error instanceof ApiError ? error.message : "Couldn't approve this document.";
          setApproveError(message);
          toast.error(message);
        },
      },
    );
  }
  const submitApprove = methods.handleSubmit(handleApprove);

  function handleSkip() {
    skipMutation.mutate(document.id, {
      onSuccess: () => advancePast(document.id),
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.message : "Couldn't skip this document.");
      },
    });
  }

  function handleTrash() {
    trashMutation.mutate(document.id, {
      onSuccess: () => toast.success("Moved to trash"),
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.message : "Couldn't trash this document.");
      },
    });
  }

  const isPendingProposal = document.proposal_status === "pending";
  const collision = folderContext.data?.filename_collision ?? false;
  const canApprove = methods.formState.isValid && !collision;

  // SPEC 8.4: no keyboard shortcuts on mobile at all. Also suppressed while the folder
  // picker or the cheat sheet itself has focus trapped, so their own key handling isn't
  // fought by this global listener.
  useReviewShortcuts(isDesktop && !isPendingProposal && !pickerOpen && !cheatSheetOpen, {
    onApprove: () => canApprove && submitApprove(),
    onSkip: handleSkip,
    onOpenFolderPicker: () => setPickerOpen(true),
    onFocusName: () => methods.setFocus(methods.getValues("handWritten") ? "name" : "sender"),
    onPrevPage: () => viewerRef.current?.prevPage(),
    onNextPage: () => viewerRef.current?.nextPage(),
    onOpenCheatSheet: () => setCheatSheetOpen(true),
  });

  const form = !isPendingProposal && (
    <FormProvider {...methods}>
      <ReviewForm
        document={document}
        folderContext={folderContext}
        onChooseFolder={() => setPickerOpen(true)}
      />
      <div className="flex flex-wrap items-center gap-4">
        {/* Offered for a ready proposal too: after changing a configuration in the Lab, the
            existing proposal is stale and this is how to get a fresh one. */}
        <button
          type="button"
          className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          onClick={() => setRerunOpen(true)}
          disabled={regenerateMutation.isPending}
        >
          {regenerateMutation.isPending ? "Re-running…" : "Re-run analysis"}
        </button>
        {document.proposal_status === "failed" && (
          <button
            type="button"
            className="text-sm font-medium text-primary underline-offset-4 hover:underline"
            onClick={() => regenerateMutation.mutate({ id: document.id })}
            disabled={regenerateMutation.isPending}
          >
            Try again
          </button>
        )}
        {/* Offered whatever the proposal status: the interesting question is often "could
            something else have read this better", which a *successful* proposal raises just
            as much as a failed one. */}
        <button
          type="button"
          className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          onClick={openComparison}
        >
          Compare methods
        </button>
      </div>
    </FormProvider>
  );

  const pendingSkeleton = (
    <div className="space-y-3" aria-busy="true">
      <p className="text-sm text-muted-foreground" aria-live="polite">
        {document.analysis_stage ?? document.waiting_reason ?? "Waiting for the AI proposal…"}
      </p>
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
    </div>
  );

  const actionBar = !isPendingProposal && (
    <ActionBar
      canApprove={canApprove}
      isApproving={approveMutation.isPending}
      isSkipping={skipMutation.isPending}
      isTrashing={trashMutation.isPending}
      approveError={approveError}
      onApprove={submitApprove}
      onSkip={handleSkip}
      onTrash={handleTrash}
    />
  );

  const dialogs = (
    <>
      <FolderPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        value={folderPath}
        onSelect={(path) =>
          methods.setValue("folderPath", path, { shouldValidate: true, shouldDirty: true })
        }
      />
      <RerunDialog
        open={rerunOpen}
        onOpenChange={setRerunOpen}
        isPending={regenerateMutation.isPending}
        currentConfigurationName={document.configuration_name}
        onRun={(choice) =>
          regenerateMutation.mutate(
            { id: document.id, choice },
            { onSuccess: () => setRerunOpen(false) },
          )
        }
      />
      <ShortcutCheatSheet open={cheatSheetOpen} onOpenChange={setCheatSheetOpen} />
      <MethodComparison
        open={compareOpen}
        onOpenChange={setCompareOpen}
        extension={document.extension}
        results={compareMutation.data?.results}
        isPending={compareMutation.isPending}
        onRerun={() => compareMutation.mutate(document.id)}
        error={
          compareMutation.error
            ? compareMutation.error instanceof ApiError
              ? compareMutation.error.message
              : "Couldn't compare the methods."
            : null
        }
        onUse={(result) => {
          if (!result.proposal) return;
          // setValue, not reset: this is an offer to start from, and every field stays
          // editable and validated exactly as if it had been typed.
          const options = { shouldValidate: true, shouldDirty: true };
          for (const [key, value] of Object.entries(formValuesFor(result.proposal))) {
            methods.setValue(key as keyof ReviewFormValues, value, options);
          }
          toast.success(`Filled in from ${result.label}`);
        }}
      />
    </>
  );

  if (isDesktop) {
    return (
      <div className="min-h-0 flex-1">
        <ResizableSplit
          left={
            <DesktopDocumentPane
              ref={viewerRef}
              documentId={document.id}
              filename={document.original_filename}
              mimeType={document.mime_type}
              fileSizeBytes={document.file_size_bytes}
              pageCount={document.page_count}
            />
          }
          right={
            <div className="flex h-full flex-col">
              <div className="flex-1 space-y-4 overflow-y-auto p-4">
                {isPendingProposal ? pendingSkeleton : form}
              </div>
              {actionBar}
            </div>
          }
        />
        {dialogs}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex-1 space-y-4 p-4">
        <DocumentViewer
          documentId={document.id}
          filename={document.original_filename}
          mimeType={document.mime_type}
          fileSizeBytes={document.file_size_bytes}
          pageCount={document.page_count}
        />
        {isPendingProposal ? pendingSkeleton : form}
      </div>
      {actionBar}
      {dialogs}
    </div>
  );
}
