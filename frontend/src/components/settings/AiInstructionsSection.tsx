import { zodResolver } from "@hookform/resolvers/zod";
import { type ReactNode, useEffect, useMemo } from "react";
import { type FieldError, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useUpdateSettings } from "@/hooks/useSettings";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api/errors";
import type { Settings } from "@/services/api/types";
import { SectionCard } from "./SectionCard";
import {
  type InstructionsFormValues,
  instructionsFormSchema,
  MAX_INSTRUCTIONS_CHARS,
} from "./settingsSchemas";

/** SPEC 8.7's AI instructions: the person's own words, added to what each task is sent. Same
 * dirty/save/discard shape as NamingSection -- see FoldersSection for why `values` is memoised
 * and why the explicit resets pass `keepDirtyValues: false`. Blank is sent as `null`. */
export function AiInstructionsSection({
  settings,
  onDirtyChange,
}: {
  settings: Settings;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const updateSettings = useUpdateSettings();
  const values = useMemo(() => toFormValues(settings), [settings]);
  const methods = useForm<InstructionsFormValues>({
    resolver: zodResolver(instructionsFormSchema),
    mode: "onChange",
    values,
    resetOptions: { keepDirtyValues: true },
  });
  const {
    register,
    control,
    handleSubmit,
    formState: { errors, isDirty },
  } = methods;
  const [filing, extraction] = useWatch({
    control,
    name: ["filing_instructions", "extraction_instructions"],
  });

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  function onSubmit(form: InstructionsFormValues) {
    updateSettings.mutate(
      {
        ...settings,
        filing_instructions: form.filing_instructions.trim() || null,
        extraction_instructions: form.extraction_instructions.trim() || null,
      },
      {
        onSuccess: (saved) => {
          methods.reset(toFormValues(saved), { keepDirtyValues: false });
          toast.success("AI instructions saved");
        },
        onError: (error) => {
          toast.error(
            error instanceof ApiError ? error.message : "Couldn't save the instructions.",
          );
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <SectionCard
        title="AI instructions"
        isDirty={isDirty}
        isSaving={updateSettings.isPending}
        onDiscard={() => methods.reset(values, { keepDirtyValues: false })}
      >
        <p className="text-sm text-muted-foreground">
          Added to what each task is sent, for every document read from now on. Documents already in
          the queue keep their proposal until you regenerate them or retry failed ones.
        </p>
        <InstructionsField
          id="filing_instructions"
          label="Filing instructions"
          help="For choosing the name, folder and date."
          length={filing.trim().length}
          error={errors.filing_instructions}
        >
          <Textarea
            id="filing_instructions"
            rows={4}
            className="max-h-64"
            placeholder="Insurance letters go under /Documents/Versicherung. Use the sender's short name."
            aria-invalid={!!errors.filing_instructions}
            aria-describedby="filing_instructions-counter"
            {...register("filing_instructions")}
          />
        </InstructionsField>
        <InstructionsField
          id="extraction_instructions"
          label="Extraction instructions"
          help="For reading the pages into markdown. Only used when an extraction endpoint is set."
          length={extraction.trim().length}
          error={errors.extraction_instructions}
        >
          <Textarea
            id="extraction_instructions"
            rows={4}
            className="max-h-64"
            placeholder="Documents are mostly German. Keep umlauts and Swiss number formats as written."
            aria-invalid={!!errors.extraction_instructions}
            aria-describedby="extraction_instructions-counter"
            {...register("extraction_instructions")}
          />
        </InstructionsField>
      </SectionCard>
    </form>
  );
}

function toFormValues(settings: Settings): InstructionsFormValues {
  return {
    filing_instructions: settings.filing_instructions ?? "",
    extraction_instructions: settings.extraction_instructions ?? "",
  };
}

/** Label, the field itself, and a help-or-error line with a character counter beside it. The
 * counter's id is `${id}-counter`, which the field names in its aria-describedby. */
function InstructionsField({
  id,
  label,
  help,
  length,
  error,
  children,
}: {
  id: string;
  label: string;
  help: string;
  length: number;
  error: FieldError | undefined;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      <div className="flex items-start justify-between gap-3 text-sm">
        {error ? (
          <p className="text-destructive">{error.message}</p>
        ) : (
          <p className="text-muted-foreground">{help}</p>
        )}
        <span
          id={`${id}-counter`}
          className={cn(
            "shrink-0 tabular-nums",
            length > MAX_INSTRUCTIONS_CHARS ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {length} / {MAX_INSTRUCTIONS_CHARS}
        </span>
      </div>
    </div>
  );
}
