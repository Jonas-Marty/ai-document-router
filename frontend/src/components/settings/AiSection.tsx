import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
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
  // Free text is the fallback, not the exception: an endpoint that cannot be reached (or one
  // that serves a model it does not list) must still be configurable.
  const [enterModelManually, setEnterModelManually] = useState(false);
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
    handleSubmit,
    getValues,
    setValue,
    watch,
    formState: { errors, isDirty },
  } = methods;

  const models = listModels.data?.models;
  const modelName = watch("ai_model_name");
  const visionModels = useFieldArray({ control: methods.control, name: "vision_model_names" });
  const showModelPicker = models !== undefined && models.length > 0 && !enterModelManually;
  // A saved model the endpoint does not list still belongs in the options -- dropping it
  // would blank a working setting the moment the picker appeared.
  const modelOptions = useMemo(() => {
    if (models === undefined) return [];
    return modelName && !models.includes(modelName) ? [modelName, ...models] : models;
  }, [models, modelName]);

  function onTest() {
    const typedKey = getValues("ai_api_key").trim();
    listModels.mutate(
      {
        ai_endpoint_url: getValues("ai_endpoint_url").trim(),
        ...(typedKey ? { ai_api_key: typedKey } : {}),
      },
      {
        onSuccess: ({ models: found }) => {
          setEnterModelManually(false);
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
          {showModelPicker ? (
            <Select
              value={modelName}
              onValueChange={(value) =>
                setValue("ai_model_name", value, { shouldDirty: true, shouldValidate: true })
              }
            >
              <SelectTrigger id="ai_model_name" className="w-full font-mono">
                <SelectValue placeholder="Choose a model" />
              </SelectTrigger>
              <SelectContent>
                {modelOptions.map((model) => (
                  <SelectItem key={model} value={model} className="font-mono">
                    {model}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Input
              id="ai_model_name"
              className="font-mono"
              aria-invalid={!!errors.ai_model_name}
              {...register("ai_model_name")}
            />
          )}
          {errors.ai_model_name && (
            <p className="text-sm text-destructive">{errors.ai_model_name.message}</p>
          )}
          {models !== undefined && models.length > 0 && (
            <Button
              type="button"
              variant="link"
              className="h-auto p-0 text-sm"
              onClick={() => setEnterModelManually(!enterModelManually)}
            >
              {showModelPicker ? "Enter a model name manually" : "Choose from the endpoint's list"}
            </Button>
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
              <div key={field.id} className="flex items-center gap-2">
                <Input
                  className="font-mono"
                  aria-label={`Vision model ${index + 1}`}
                  placeholder="qwen2.5vl:7b"
                  {...register(`vision_model_names.${index}.value`)}
                />
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
          {listModels.isError && (
            <p className="text-sm text-destructive">
              {listModels.error instanceof ApiError
                ? listModels.error.message
                : "Couldn't reach the AI endpoint."}
            </p>
          )}
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
