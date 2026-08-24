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
import { type AiFormValues, aiFormSchema } from "./settingsSchemas";

/** SPEC 8.7's AI section. The key itself is never returned by the API (CLAUDE.md rule 5:
 * write-only) -- `ai_api_key` always starts and (after a save) ends empty; only
 * `ai_api_key_set` says whether one is stored. `values` can't include the real key at all, so
 * unlike Folders/Naming, a save here can't rely on the field naturally matching its `values`
 * baseline again to clear `isDirty` -- that baseline is always `""`, never what was typed.
 * A full `reset()` from the mutation response after a successful save is what actually
 * clears it (all three fields re-baseline together, same as Folders/Naming). See
 * FoldersSection's comment on `resetOptions.keepDirtyValues`: it protects the reactive
 * values-sync from clobbering an in-progress edit, but is also the default for explicit
 * `reset()` calls unless overridden -- both explicit resets below pass
 * `{ keepDirtyValues: false }` so a save/discard actually applies to the dirty field
 * (otherwise the typed key would still show after a successful save). */
export function AiSection({
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
      ai_endpoint_url: settings.ai_endpoint_url,
      ai_model_name: settings.ai_model_name,
      ai_api_key: "",
    }),
    [settings],
  );
  const methods = useForm<AiFormValues>({
    resolver: zodResolver(aiFormSchema),
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

  function onSubmit(values: AiFormValues) {
    const typedKey = values.ai_api_key.trim();
    const { ai_api_key_set: _aiApiKeySet, ...rest } = settings;
    updateSettings.mutate(
      {
        ...rest,
        ai_endpoint_url: values.ai_endpoint_url.trim(),
        ai_model_name: values.ai_model_name.trim(),
        ...(typedKey ? { ai_api_key: typedKey } : {}),
      },
      {
        onSuccess: (saved) => {
          methods.reset(
            {
              ai_endpoint_url: saved.ai_endpoint_url,
              ai_model_name: saved.ai_model_name,
              ai_api_key: "",
            },
            { keepDirtyValues: false },
          );
          toast.success("AI settings saved");
        },
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Couldn't save AI settings.");
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <SectionCard
        title="AI"
        isDirty={isDirty}
        isSaving={updateSettings.isPending}
        onDiscard={() => methods.reset(values, { keepDirtyValues: false })}
      >
        <div className="space-y-1.5">
          <Label htmlFor="ai_endpoint_url">Endpoint URL</Label>
          <Input
            id="ai_endpoint_url"
            className="font-mono"
            aria-invalid={!!errors.ai_endpoint_url}
            {...register("ai_endpoint_url")}
          />
          {errors.ai_endpoint_url && (
            <p className="text-sm text-destructive">{errors.ai_endpoint_url.message}</p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ai_model_name">Model</Label>
          <Input
            id="ai_model_name"
            className="font-mono"
            aria-invalid={!!errors.ai_model_name}
            {...register("ai_model_name")}
          />
          {errors.ai_model_name && (
            <p className="text-sm text-destructive">{errors.ai_model_name.message}</p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ai_api_key">API key</Label>
          <Input
            id="ai_api_key"
            type="password"
            autoComplete="off"
            placeholder={settings.ai_api_key_set ? "••••••••  (saved)" : ""}
            {...register("ai_api_key")}
          />
          <p className="text-sm text-muted-foreground">Leave blank to keep the current key.</p>
        </div>
      </SectionCard>
    </form>
  );
}
