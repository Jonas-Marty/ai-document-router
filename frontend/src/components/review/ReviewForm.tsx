import { useMemo } from "react";
import { useFormContext } from "react-hook-form";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { useFolderContext } from "@/hooks/useFolders";
import { useKnowledge, useKnownSenders } from "@/hooks/useKnowledge";
import { useSettings } from "@/hooks/useSettings";
import {
  FIELD_LABELS,
  NAME_FIELDS,
  type NameFieldKey,
  REQUIRED_FIELDS,
  trimStemOnBlur,
} from "@/lib/naming";
import { relativeTime } from "@/lib/relativeTime";
import type { Document } from "@/services/api/types";
import { ConfidenceBadge, isLowConfidence } from "./ConfidenceBadge";
import { ExtractedText } from "./ExtractedText";
import { effectiveName, type ReviewFormValues, rememberPrompts } from "./reviewFormSchema";
import { SiblingList } from "./SiblingList";

export interface ReviewFormProps {
  document: Document;
  folderContext: ReturnType<typeof useFolderContext>;
  onChooseFolder: () => void;
  /** SPEC 8.3 item 5, placed between the folder and the siblings. */
  producedBy?: React.ReactNode;
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

/** One of the name's fields (SPEC 7.1a), trimmed on blur like the name itself. */
function NameFieldInput({ field }: { field: NameFieldKey }) {
  const {
    register,
    setValue,
    formState: { errors },
  } = useFormContext<ReviewFormValues>();
  const registered = register(field);
  const error = errors[field];
  const id = `review-${field}`;
  const optional = !REQUIRED_FIELDS.has(field);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>
        {FIELD_LABELS[field]}
        {optional && <span className="font-normal text-muted-foreground"> (optional)</span>}
      </Label>
      <Input
        id={id}
        aria-invalid={!!error}
        {...registered}
        onBlur={(e) => {
          registered.onBlur(e);
          const trimmed = e.target.value.trim();
          if (trimmed !== e.target.value) {
            setValue(field, trimmed, { shouldValidate: true, shouldDirty: true });
          }
        }}
      />
      {error && <p className="text-sm text-destructive">{error.message}</p>}
    </div>
  );
}

/** SPEC 8.3 3a. Unticked to start: nothing is learned that the owner did not ask for. */
function RememberCheckbox({
  field,
  children,
}: {
  field: "rememberSender" | "rememberAccount";
  children: React.ReactNode;
}) {
  const { register } = useFormContext<ReviewFormValues>();
  return (
    <label className="flex min-h-11 items-center gap-2 text-sm break-all sm:min-h-0">
      <input type="checkbox" className="size-4 shrink-0 accent-primary" {...register(field)} />
      <span>{children}</span>
    </label>
  );
}

/** SPEC 8.3's field order 1-6 (item 5 comes in as `producedBy`) (order 7, the actions, lives in the sticky/inline action bar
 * that shares this form's react-hook-form instance via FormProvider -- see ReviewPage). */
export function ReviewForm({
  document,
  folderContext,
  onChooseFolder,
  producedBy,
}: ReviewFormProps) {
  const {
    register,
    watch,
    setValue,
    formState: { errors },
  } = useFormContext<ReviewFormValues>();
  const { data: settings } = useSettings();
  const template = useKnowledge().data?.name_template;
  const knownSenders = useKnownSenders().data;
  const nameField = register("name");
  const values = watch();
  const folderPath = values.folderPath;
  const handWritten = values.handWritten;
  const name = effectiveName(values, template);
  // The fields in the order the name puts them; one the template leaves out is not asked for.
  const fieldOrder = template
    ? template.filter((key): key is NameFieldKey => key !== "date")
    : NAME_FIELDS;
  const asks = rememberPrompts(values, knownSenders);

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
          <ConfidenceBadge score={document.proposal.confidence_score} />
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
        <Input
          id="review-document-date"
          type="date"
          aria-invalid={!!errors.documentDate}
          {...register("documentDate")}
        />
        {errors.documentDate && (
          <p className="text-sm text-destructive">{errors.documentDate.message}</p>
        )}
      </div>

      {!handWritten && (
        <div className="grid gap-3 sm:grid-cols-2">
          {fieldOrder.map((key) => (
            <NameFieldInput key={key} field={key} />
          ))}
        </div>
      )}

      <div className="space-y-1.5">
        <Label htmlFor={handWritten ? "review-name" : undefined}>File name</Label>
        <div className="flex items-center gap-2">
          {handWritten ? (
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
          ) : (
            // The live result of the fields above; what the backend assembles again on approve.
            <output
              aria-label="Assembled file name"
              className="min-h-9 min-w-0 flex-1 break-words rounded-md border border-dashed border-border px-2.5 py-1.5 font-mono text-sm"
            >
              {name || <span className="text-muted-foreground">Fill in the fields above</span>}
            </output>
          )}
          <span className="shrink-0 rounded-md border border-border bg-muted px-2.5 py-1.5 font-mono text-sm text-muted-foreground">
            {document.extension}
          </span>
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={handWritten}
            onChange={(e) => {
              const options = { shouldValidate: true, shouldDirty: true };
              // Start the hand-written name from what the fields made, not from blank. Leaving
              // hand-writing keeps the fields as they were.
              if (e.target.checked) setValue("name", name, options);
              setValue("handWritten", e.target.checked, options);
            }}
          />
          Write the name by hand
        </label>
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

      {(asks.sender || asks.account) && (
        <fieldset className="space-y-1 rounded-md border border-border px-3 py-2">
          <legend className="px-1 text-sm font-medium">Remember for next time</legend>
          {asks.sender && (
            <RememberCheckbox field="rememberSender">
              Remember “{values.sender.trim()}” as a sender
            </RememberCheckbox>
          )}
          {asks.account && (
            <RememberCheckbox field="rememberAccount">
              Remember {values.account.trim()} for {values.sender.trim()}
            </RememberCheckbox>
          )}
        </fieldset>
      )}

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

      {producedBy}
      <ExtractedText document={document} />

      <div className="space-y-1.5">
        <Label>Files already in this folder</Label>
        <SiblingList query={folderContext} />
      </div>
    </div>
  );
}
