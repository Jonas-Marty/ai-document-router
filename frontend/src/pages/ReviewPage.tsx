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
import { ProducedBy } from "@/components/review/ProducedBy";
import { RerunDialog } from "@/components/review/RerunDialog";
import { ResizableSplit } from "@/components/review/ResizableSplit";
import { ReviewForm } from "@/components/review/ReviewForm";
import { ReviewList } from "@/components/review/ReviewList";
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
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useIsDesktop, useIsWide } from "@/hooks/useBreakpoint";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import {
  useApproveDocument,
  useCompareDocument,
  useRegenerateDocument,
  useSkipDocument,
  useTrashDocument,
} from "@/hooks/useDocument";
import { useFolderContext } from "@/hooks/useFolders";
import { useKnowledge, useKnownSenders } from "@/hooks/useKnowledge";
import { useReviewList } from "@/hooks/useReviewList";
import { useReviewShortcuts } from "@/hooks/useReviewShortcuts";
import { ApiError } from "@/services/api/errors";
import type { Document } from "@/services/api/types";

function isOpen(document: Document): boolean {
  return document.status === "pending" || document.status === "skipped";
}

export default function ReviewPage() {
  const { id: requestedId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const review = useReviewList(requestedId);
  const isWide = useIsWide();
  const [listOpen, setListOpen] = useState(false);
  const { list, document, currentId } = review;

  // SPEC 8.8: a reload or a shared link lands on the same document, so the URL names the
  // current one as soon as there is one.
  useEffect(() => {
    if (requestedId === undefined && currentId !== undefined) review.show(currentId);
  }, [requestedId, currentId, review.show]);

  const firstId = review.cards.find((card) => card.id !== currentId)?.id;
  let body: React.ReactNode;
  if (list.isLoading || (currentId !== undefined && document.isLoading)) {
    body = <ReviewSkeleton />;
  } else if (currentId === undefined) {
    body = list.isError ? (
      <div className="p-4">
        <ErrorState
          message={list.error instanceof ApiError ? list.error.message : "Couldn't load the list."}
          onRetry={() => list.refetch()}
        />
      </div>
    ) : (
      <div className="p-4">
        <EmptyState
          title={review.filters.filtered ? "Nothing matches these filters." : "Nothing to review."}
          description="New scans appear in the Inbox automatically."
          action={{ label: "Back to the Inbox", onClick: () => navigate("/") }}
        />
      </div>
    );
  } else if (document.isError || (document.data && !isOpen(document.data))) {
    // SPEC 8.8: filed or trashed meanwhile -- in another tab, by another device -- or never
    // the person's to see. A 404 answers both, and neither is worth more than this.
    const gone =
      document.isError && !(document.error instanceof ApiError && document.error.status === 404);
    body = (
      <div className="p-4">
        {gone ? (
          <ErrorState
            message={
              document.error instanceof ApiError
                ? document.error.message
                : "Couldn't load this document."
            }
            onRetry={() => document.refetch()}
          />
        ) : (
          <EmptyState
            title="This document has already been filed or trashed."
            action={
              firstId
                ? { label: "Review the next one", onClick: () => review.show(firstId) }
                : { label: "Back to the Inbox", onClick: () => navigate("/") }
            }
          />
        )}
      </div>
    );
  } else if (document.data) {
    body = <DocumentReview key={document.data.id} document={document.data} review={review} />;
  }

  return (
    <div className="flex h-full min-h-0">
      {isWide && (
        <aside
          aria-label="Ready for review"
          className="flex w-80 shrink-0 flex-col border-r border-border"
        >
          <ReviewList review={review} />
        </aside>
      )}
      <div className="mx-auto flex h-full min-w-0 max-w-6xl flex-1 flex-col lg:max-w-none">
        <div className="flex items-center justify-between gap-2 p-4 pb-0">
          <h1 className="text-lg font-semibold">Review</h1>
          {/* The count is in the label rather than behind the click: "what is still open?"
              is answered without opening anything. */}
          {!isWide && (
            <Button variant="outline" size="sm" onClick={() => setListOpen(true)}>
              <ListChecks className="size-4" aria-hidden="true" />
              Ready
              <span className="rounded bg-secondary px-1.5 py-0.5 text-xs font-medium tabular-nums text-secondary-foreground">
                {review.readyCount}
              </span>
            </Button>
          )}
        </div>
        {body}
      </div>
      {!isWide && (
        <Sheet open={listOpen} onOpenChange={setListOpen}>
          <SheetContent
            side="left"
            className="flex flex-col gap-0 p-0 data-[side=left]:w-full data-[side=left]:sm:max-w-sm"
            // Focusing the search box would raise the phone keyboard over the list.
            onOpenAutoFocus={(event) => event.preventDefault()}
          >
            <SheetHeader className="p-4 pb-3">
              <SheetTitle>Ready for review ({review.readyCount})</SheetTitle>
            </SheetHeader>
            {/* Closing on a pick: left open, the list would cover the document just chosen. */}
            <ReviewList review={review} onPick={() => setListOpen(false)} />
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}

function ReviewSkeleton() {
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
  review,
}: {
  document: Document;
  review: ReturnType<typeof useReviewList>;
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

  /** SPEC 8.8: on success the next row becomes current -- picked now, while this row is
   * still in the list to count from. A failure never gets to `moveOn`. */
  function moveOnFrom(id: string) {
    const next = review.nextAfter(id);
    return () => review.show(next);
  }

  function handleApprove(values: ReviewFormValues) {
    setApproveError(null);
    const moveOn = moveOnFrom(document.id);
    approveMutation.mutate(
      {
        id: document.id,
        body: approveRequestFor(values, rememberPrompts(values, knownSenders)),
      },
      {
        onSuccess: (response) => {
          toast.success(`Moved to ${response.history_entry.final_folder_path}`);
          moveOn();
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

  function handleLater() {
    const moveOn = moveOnFrom(document.id);
    skipMutation.mutate(document.id, {
      onSuccess: moveOn,
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.message : "Couldn't put this off.");
      },
    });
  }

  function handleTrash() {
    const moveOn = moveOnFrom(document.id);
    trashMutation.mutate(document.id, {
      onSuccess: () => {
        toast.success("Moved to trash");
        moveOn();
      },
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.message : "Couldn't trash this document.");
      },
    });
  }

  /** SPEC 8.8: the document goes back to To analyse and the next row becomes current. One
   * opened from outside the list -- a failed analysis, from To analyse -- stays on screen
   * instead: the person came to watch that one. */
  function reanalyse(configurationId?: string) {
    const moveOn = review.inList(document.id) ? moveOnFrom(document.id) : null;
    regenerateMutation.mutate(
      {
        id: document.id,
        choice: configurationId ? { configuration_id: configurationId } : undefined,
      },
      {
        onSuccess: () => {
          setRerunOpen(false);
          moveOn?.();
        },
        onError: (error) => {
          toast.error(
            error instanceof ApiError ? error.message : "Couldn't ask for a new analysis.",
          );
        },
      },
    );
  }

  const isPendingProposal = document.proposal_status === "pending";
  const collision = folderContext.data?.filename_collision ?? false;
  const canApprove = methods.formState.isValid && !collision;

  // SPEC 8.4: no keyboard shortcuts on mobile at all. Also suppressed while the folder
  // picker or the cheat sheet itself has focus trapped, so their own key handling isn't
  // fought by this global listener.
  useReviewShortcuts(isDesktop && !isPendingProposal && !pickerOpen && !cheatSheetOpen, {
    onApprove: () => canApprove && submitApprove(),
    onLater: handleLater,
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
        producedBy={
          <ProducedBy
            document={document}
            isReanalysing={regenerateMutation.isPending}
            onReanalyse={() => reanalyse()}
            onReanalyseWith={() => setRerunOpen(true)}
            onCompare={openComparison}
          />
        }
      />
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
      isLater={skipMutation.isPending}
      isTrashing={trashMutation.isPending}
      approveError={approveError}
      onApprove={submitApprove}
      onLater={handleLater}
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
        onRun={(choice) => reanalyse(choice.configuration_id)}
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
