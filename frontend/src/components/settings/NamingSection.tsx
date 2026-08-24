import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useUpdateSettings } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";
import type { Settings } from "@/services/api/types";
import { SectionCard } from "./SectionCard";
import { type NamingFormValues, namingFormSchema } from "./settingsSchemas";

/** SPEC 8.7's Naming section: the filename pattern (with a live regex validity check via
 * `namingFormSchema`, `mode: "onChange"` so the error appears as the user types rather than
 * only on save) and its hint text. An empty pattern is sent as `null`, not `""` -- an empty
 * string compiles as a regex that matches everything, so the backend would silently accept it
 * and SPEC 7.1's mismatch warning would never fire again for any name. */
export function NamingSection({
  settings,
  onDirtyChange,
}: {
  settings: Settings;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const updateSettings = useUpdateSettings();
  // See FoldersSection's comment: `values` must stay reference-stable across incidental
  // re-renders, not a fresh object every time.
  const values = useMemo(
    () => ({
      filename_pattern: settings.filename_pattern ?? "",
      filename_pattern_hint: settings.filename_pattern_hint ?? "",
    }),
    [settings],
  );
  // See FoldersSection's comment on `resetOptions.keepDirtyValues`: it protects the reactive
  // values-sync, but is also react-hook-form's default for explicit `reset()` calls unless
  // overridden -- both explicit resets below (Discard, post-save) pass
  // `{ keepDirtyValues: false }` so they actually apply to whichever field is dirty.
  const methods = useForm<NamingFormValues>({
    resolver: zodResolver(namingFormSchema),
    mode: "onChange",
    values,
    resetOptions: { keepDirtyValues: true },
  });
  const {
    register,
    handleSubmit,
    formState: { errors, isDirty },
  } = methods;

  useEffect(() => {
    onDirtyChange(isDirty);
  }, [isDirty, onDirtyChange]);

  function onSubmit(values: NamingFormValues) {
    const trimmedPattern = values.filename_pattern.trim();
    const { ai_api_key_set: _aiApiKeySet, ...rest } = settings;
    updateSettings.mutate(
      {
        ...rest,
        filename_pattern: trimmedPattern || null,
        filename_pattern_hint: values.filename_pattern_hint.trim() || null,
      },
      {
        onSuccess: (saved) => {
          methods.reset(
            {
              filename_pattern: saved.filename_pattern ?? "",
              filename_pattern_hint: saved.filename_pattern_hint ?? "",
            },
            { keepDirtyValues: false },
          );
          toast.success("Naming saved");
        },
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Couldn't save naming.");
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <SectionCard
        title="Naming"
        isDirty={isDirty}
        isSaving={updateSettings.isPending}
        onDiscard={() => methods.reset(values, { keepDirtyValues: false })}
      >
        <div className="space-y-1.5">
          <Label htmlFor="filename_pattern">Filename pattern</Label>
          <Input
            id="filename_pattern"
            className="font-mono"
            placeholder="^\d{4}\.\d{2}\.\d{2} "
            aria-invalid={!!errors.filename_pattern}
            {...register("filename_pattern")}
          />
          {errors.filename_pattern ? (
            <p className="text-sm text-destructive">{errors.filename_pattern.message}</p>
          ) : (
            <p className="text-sm text-muted-foreground">
              A regular expression. Names that don't match get a warning, not a block.
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="filename_pattern_hint">Pattern hint</Label>
          <Input
            id="filename_pattern_hint"
            placeholder="Expected YYYY.MM.DD prefix"
            {...register("filename_pattern_hint")}
          />
          <p className="text-sm text-muted-foreground">
            Shown next to the mismatch warning on the review form.
          </p>
        </div>
      </SectionCard>
    </form>
  );
}
