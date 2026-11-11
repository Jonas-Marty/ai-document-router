import { useMemo } from "react";
import { useFormContext } from "react-hook-form";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { useFolderContext } from "@/hooks/useFolders";
import { useSettings } from "@/hooks/useSettings";
import { trimStemOnBlur } from "@/lib/naming";
import { relativeTime } from "@/lib/relativeTime";
import type { Document } from "@/services/api/types";
import { ConfidenceBadge, isLowConfidence } from "./ConfidenceBadge";
import { ExtractedText } from "./ExtractedText";
import { ModelChip, ModelHint } from "./ModelChip";
import { PromptDetails } from "./PromptDetails";
import { ReasoningBlock } from "./ReasoningBlock";
import type { ReviewFormValues } from "./reviewFormSchema";
import { SiblingList } from "./SiblingList";
import { TokenUsage } from "./TokenUsage";

export interface ReviewFormProps {
  document: Document;
  folderContext: ReturnType<typeof useFolderContext>;
  onChooseFolder: () => void;
}

function compileFilenamePattern(pattern: string | null): RegExp | null {
  if (!pattern) return null;
  try {
    return new RegExp(pattern);
  } catch {
    // A pattern that doesn't compile as a JS RegExp (e.g. a Python-only construct) can't be
    // checked client-side. SPEC 7.1 makes this warning-only anyway, so failing open (no
    // warning shown) is safe -- the backend never re-validates against filename_pattern, it's
    // purely a frontend hint.
    return null;
  }
}

/** SPEC 8.3's field order 1-6 (order 7, the actions, lives in the sticky/inline action bar
 * that shares this form's react-hook-form instance via FormProvider -- see ReviewPage). */
export function ReviewForm({ document, folderContext, onChooseFolder }: ReviewFormProps) {
  const {
    register,
    watch,
    setValue,
    formState: { errors },
  } = useFormContext<ReviewFormValues>();
  const { data: settings } = useSettings();
  const nameField = register("name");
  const folderPath = watch("folderPath");
  const name = watch("name");

  const patternRe = useMemo(
    () => compileFilenamePattern(settings?.filename_pattern ?? null),
    [settings?.filename_pattern],
  );
  const patternMismatch = patternRe !== null && name.length > 0 && !patternRe.test(name);
  const collision = folderContext.data?.filename_collision ?? false;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        {document.proposal ? (
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <ConfidenceBadge score={document.proposal.confidence_score} />
            <ModelChip proposal={document.proposal} />
          </div>
        ) : document.proposal_error ? (
          <p className="text-sm text-destructive">{document.proposal_error}</p>
        ) : (
          <span />
        )}
        <span className="shrink-0 text-xs text-muted-foreground">
          {relativeTime(document.scanned_at)}
        </span>
      </div>

      {document.proposal && isLowConfidence(document.proposal.confidence_score) && (
        <div
          role="alert"
          className="rounded-md border border-amber-600/30 bg-amber-100 px-3 py-2 text-sm text-amber-900 dark:border-amber-400/30 dark:bg-amber-950 dark:text-amber-200"
        >
          Low confidence — check the folder and date.
        </div>
      )}

      {settings?.store_ocr_text && document.ocr_status === "ready" && (
        <p className="text-sm text-muted-foreground">
          This scan had no text layer, so a searchable copy is filed in its place. The pages are
          unchanged — only invisible text is added.
        </p>
      )}
      {document.ocr_status === "failed" && document.ocr_error && (
        <p className="text-sm text-amber-600 dark:text-amber-400">
          Couldn't make this scan searchable: {document.ocr_error} It will be filed as it is.
        </p>
      )}

      <div className="space-y-1.5">
        <Label htmlFor="review-document-date">Document date</Label>
        <Input id="review-document-date" type="date" {...register("documentDate")} />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="review-name">File name</Label>
        <div className="flex items-center gap-2">
          <Input
            id="review-name"
            className="min-w-0 flex-1 font-mono"
            // Long names are clipped in the field; the native tooltip shows the whole name.
            title={name}
            aria-invalid={!!errors.name || collision}
            {...nameField}
            onBlur={(e) => {
              nameField.onBlur(e);
              const trimmed = trimStemOnBlur(e.target.value);
              if (trimmed !== e.target.value) {
                setValue("name", trimmed, { shouldValidate: true, shouldDirty: true });
              }
            }}
          />
          <span className="shrink-0 rounded-md border border-border bg-muted px-2.5 py-1.5 font-mono text-sm text-muted-foreground">
            {document.extension}
          </span>
        </div>
        {errors.name ? (
          <p className="text-sm text-destructive">{errors.name.message}</p>
        ) : collision ? (
          <p className="text-sm text-destructive">
            A file named "{name}
            {document.extension}" already exists in this folder. Choose a different name.
          </p>
        ) : patternMismatch ? (
          // The hint is often the full naming guide the model is also given; a paragraph of
          // amber under the field buries the form, so it stays one click away.
          <details className="text-sm text-amber-600 dark:text-amber-400">
            <summary className="cursor-pointer">
              This name doesn't match your naming pattern.
              {settings?.filename_pattern_hint && " Show the pattern"}
            </summary>
            {settings?.filename_pattern_hint && (
              <p className="mt-1 whitespace-pre-wrap">{settings.filename_pattern_hint}</p>
            )}
          </details>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <Label>Target folder</Label>
        <button
          type="button"
          onClick={onChooseFolder}
          className="flex w-full min-h-11 items-center justify-between gap-2 rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-left font-mono text-sm transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="min-w-0 truncate">{folderPath || "Choose a folder"}</span>
          <span className="shrink-0 font-sans text-xs text-muted-foreground">Change</span>
        </button>
        {errors.folderPath && (
          <p className="text-sm text-destructive">{errors.folderPath.message}</p>
        )}
      </div>

      {document.proposal && <ReasoningBlock text={document.proposal.reasoning_text} />}
      {document.proposal && <ModelHint proposal={document.proposal} />}
      {document.proposal && <PromptDetails proposal={document.proposal} />}
      <ExtractedText document={document} />
      {/* Outside the proposal check: a failed analysis spent tokens too. */}
      <TokenUsage usage={document.analysis_usage} />

      <div className="space-y-1.5">
        <Label>Files already in this folder</Label>
        <SiblingList query={folderContext} />
      </div>
    </div>
  );
}
