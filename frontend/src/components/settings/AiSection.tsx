import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useListAiModels, useUpdateSettings } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";
import type { Settings } from "@/services/api/types";
import { SectionCard } from "./SectionCard";
import { type AiFormValues, aiFormSchema } from "./settingsSchemas";

/** A saved/typed value that the endpoint's list may not (yet, or ever) contain still belongs
 * in the options -- dropping it would blank a working setting the moment the picker appeared,
 * or before it has fetched anything at all. */
function optionsWithCurrent(models: string[] | undefined, current: string): string[] {
  const known = models ?? [];
  return current && !known.includes(current) ? [current, ...known] : known;
}

/** One model dropdown: the Model field and each "vision models to compare" row all share
 * this. Fetching is lazy -- opening the dropdown is what triggers it, not a prior click on
 * Test Connection, so `onOpen` is called every time and it is up to the caller (the shared
 * `useListAiModels` mutation) to no-op once it already has a result. Free text stays reachable
 * behind a toggle: an endpoint that cannot be reached, or one that does not list the model you
 * actually want, must still be configurable (CLAUDE.md rule 8 -- no picker can be the only
 * way in). */
function ModelPicker({
  id,
  ariaLabel,
  value,
  onChange,
  options,
  disabled,
  isPending,
  isError,
  errorMessage,
  onOpen,
  manualMode,
  onToggleManual,
}: {
  id?: string;
  ariaLabel?: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
  disabled: boolean;
  isPending: boolean;
  isError: boolean;
  errorMessage: string | null;
  onOpen: () => void;
  manualMode: boolean;
  onToggleManual: () => void;
}) {
  if (manualMode) {
    return (
      <div className="space-y-1">
        <Input
          id={id}
          aria-label={ariaLabel}
          className="font-mono"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <Button
          type="button"
          variant="link"
          className="h-auto p-0 text-sm"
          onClick={onToggleManual}
        >
          Choose from the endpoint's list
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <Select
        value={value}
        onValueChange={onChange}
        disabled={disabled}
        onOpenChange={(open) => {
          if (open) onOpen();
        }}
      >
        <SelectTrigger id={id} aria-label={ariaLabel} className="w-full font-mono">
          {isPending ? (
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              Loading models…
            </span>
          ) : (
            <SelectValue placeholder="Choose a model" />
          )}
        </SelectTrigger>
        <SelectContent>
          {/* An item still renders for the current value even on error or an empty result --
           * dropping it would leave Radix with nothing to resolve the trigger's label from,
           * blanking a perfectly valid, already-saved model name. */}
          {isError && <p className="px-2 py-1.5 text-sm text-destructive">{errorMessage}</p>}
          {options.length === 0 ? (
            <p className="px-2 py-1.5 text-sm text-muted-foreground">No models found.</p>
          ) : (
            options.map((model) => (
              <SelectItem key={model} value={model} className="font-mono">
                {model}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
      <Button type="button" variant="link" className="h-auto p-0 text-sm" onClick={onToggleManual}>
        Enter a model name manually
      </Button>
    </div>
  );
}

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
  const listModels = useListAiModels();
  const [enterModelManually, setEnterModelManually] = useState(false);
  // Keyed by useFieldArray's row id, not index: rows can be removed, and an index-based set
  // would silently relabel a later row's manual toggle onto an earlier one.
  const [manualVisionRows, setManualVisionRows] = useState<Set<string>>(new Set());
  // See FoldersSection's comment: `values` must stay reference-stable across incidental
  // re-renders, not a fresh object every time.
  const values = useMemo(
    () => ({
      ai_endpoint_url: settings.ai_endpoint_url,
      ai_model_name: settings.ai_model_name,
      vision_model_names: settings.vision_model_names.map((value) => ({ value })),
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
    control,
    handleSubmit,
    getValues,
    watch,
    formState: { errors, isDirty },
  } = methods;

  const models = listModels.data?.models;
  const modelName = watch("ai_model_name");
  const endpointUrl = watch("ai_endpoint_url");
  const visionModels = useFieldArray({ control, name: "vision_model_names" });
  // Fetching a model list needs somewhere to send the request -- the key is optional (plenty
  // of self-hosted endpoints take none, and the backend falls back to the saved one anyway).
  const canFetchModels = endpointUrl.trim().length > 0;
  const modelErrorMessage =
    listModels.error instanceof ApiError
      ? listModels.error.message
      : "Couldn't reach the AI endpoint.";

  // Shared by every dropdown: only actually fetches once (until it errors, when the next
  // open tries again), no matter which picker's `onOpen` triggered it.
  function fetchModelsIfNeeded() {
    if (!canFetchModels || listModels.isPending || listModels.data !== undefined) return;
    const typedKey = getValues("ai_api_key").trim();
    listModels.mutate({
      ai_endpoint_url: getValues("ai_endpoint_url").trim(),
      ...(typedKey ? { ai_api_key: typedKey } : {}),
    });
  }

  function onTest() {
    const typedKey = getValues("ai_api_key").trim();
    listModels.mutate(
      {
        ai_endpoint_url: getValues("ai_endpoint_url").trim(),
        ...(typedKey ? { ai_api_key: typedKey } : {}),
      },
      {
        onSuccess: ({ models: found }) => {
          toast.success(
            found.length > 0
              ? `Endpoint reachable — ${found.length} model${found.length === 1 ? "" : "s"} available`
              : "Endpoint reachable, but it listed no models",
          );
        },
        onError: (error) => {
          toast.error(
            error instanceof ApiError ? error.message : "Couldn't reach the AI endpoint.",
          );
        },
      },
    );
  }

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
        vision_model_names: values.vision_model_names
          .map((entry) => entry.value.trim())
          .filter(Boolean),
        ...(typedKey ? { ai_api_key: typedKey } : {}),
      },
      {
        onSuccess: (saved) => {
          methods.reset(
            {
              ai_endpoint_url: saved.ai_endpoint_url,
              ai_model_name: saved.ai_model_name,
              vision_model_names: saved.vision_model_names.map((value) => ({ value })),
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
          <ModelPicker
            id="ai_model_name"
            value={modelName}
            onChange={(value) =>
              methods.setValue("ai_model_name", value, { shouldDirty: true, shouldValidate: true })
            }
            options={optionsWithCurrent(models, modelName)}
            disabled={!canFetchModels}
            isPending={listModels.isPending}
            isError={listModels.isError}
            errorMessage={modelErrorMessage}
            onOpen={fetchModelsIfNeeded}
            manualMode={enterModelManually}
            onToggleManual={() => setEnterModelManually((current) => !current)}
          />
          {errors.ai_model_name && (
            <p className="text-sm text-destructive">{errors.ai_model_name.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label>Vision models to compare</Label>
          <p className="text-sm text-muted-foreground">
            Offered on the review screen's <em>Compare methods</em> view, which reads a document
            every configured way so you can see which is worth using. Never used for ordinary filing
            — that always uses the model above.
          </p>
          <div className="space-y-2">
            {visionModels.fields.map((field, index) => (
              <div key={field.id} className="flex items-start gap-2">
                <div className="flex-1">
                  <Controller
                    control={control}
                    name={`vision_model_names.${index}.value`}
                    render={({ field: controllerField }) => (
                      <ModelPicker
                        ariaLabel={`Vision model ${index + 1}`}
                        value={controllerField.value}
                        onChange={controllerField.onChange}
                        options={optionsWithCurrent(models, controllerField.value)}
                        disabled={!canFetchModels}
                        isPending={listModels.isPending}
                        isError={listModels.isError}
                        errorMessage={modelErrorMessage}
                        onOpen={fetchModelsIfNeeded}
                        manualMode={manualVisionRows.has(field.id)}
                        onToggleManual={() =>
                          setManualVisionRows((current) => {
                            const next = new Set(current);
                            if (next.has(field.id)) {
                              next.delete(field.id);
                            } else {
                              next.add(field.id);
                            }
                            return next;
                          })
                        }
                      />
                    )}
                  />
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => visionModels.remove(index)}
                  aria-label="Remove vision model"
                >
                  <X aria-hidden="true" />
                </Button>
              </div>
            ))}
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => visionModels.append({ value: "" })}
          >
            <Plus aria-hidden="true" />
            Add vision model
          </Button>
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
        <div className="space-y-1.5">
          <Button type="button" variant="outline" disabled={listModels.isPending} onClick={onTest}>
            {listModels.isPending && <Loader2 className="animate-spin" />}
            Test connection
          </Button>
          {listModels.isError && <p className="text-sm text-destructive">{modelErrorMessage}</p>}
          {models !== undefined && (
            <p className="text-sm text-muted-foreground">
              {models.length > 0
                ? `${models.length} model${models.length === 1 ? "" : "s"} offered by this endpoint.`
                : "The endpoint answered but listed no models. Enter the model name manually."}
            </p>
          )}
        </div>
      </SectionCard>
    </form>
  );
}
