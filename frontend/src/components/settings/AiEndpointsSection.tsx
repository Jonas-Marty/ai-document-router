import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { toast } from "sonner";
import { ErrorState } from "@/components/shared/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAiEndpoints,
  useCreateAiEndpoint,
  useDeleteAiEndpoint,
  useUpdateAiEndpoint,
} from "@/hooks/useAiConfig";
import { useListAiModels } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";
import type { AiEndpoint, AiJsonMode, AiModelPrice } from "@/services/api/types";
import { ModelPicker, optionsWithCurrent } from "./ModelPicker";
import { type EndpointFormValues, endpointFormSchema, optionalNumber } from "./settingsSchemas";

const JSON_MODE_LABELS: Record<AiJsonMode, string> = {
  auto: "Automatic",
  json_schema: "JSON schema",
  json_object: "JSON object",
  none: "None",
};

/** "gpt-4.1: 0.4 in · 1.6 out per 1M". A missing side bills nothing, as the backend counts it. */
function priceLabel(price: AiModelPrice): string {
  const input = price.price_input_per_million ?? 0;
  const output = price.price_output_per_million ?? 0;
  return `${price.model_name}: ${input} in · ${output} out per 1M`;
}

/** SPEC 8.7: the places the app can send a model request, each with a name of its own.
 *
 * Not a SectionCard: an endpoint is its own resource with its own create/update/delete, so
 * each one saves when *it* is saved rather than sharing a single Save button with the rest of
 * the section. Which model each step uses is set per configuration, in the Lab. */
export function AiEndpointsSection({ onDirtyChange }: { onDirtyChange: (dirty: boolean) => void }) {
  const endpoints = useAiEndpoints();
  const deleteEndpoint = useDeleteAiEndpoint();
  // `null` means the add form; a string is the id being edited; `undefined` means neither.
  const [editing, setEditing] = useState<string | null | undefined>(undefined);
  const [confirmDelete, setConfirmDelete] = useState<AiEndpoint | null>(null);

  // An open form is unsaved work, and the navigation guard should treat it as such even
  // before anything is typed into it -- there is nothing on screen that says otherwise.
  useEffect(() => {
    onDirtyChange(editing !== undefined);
  }, [editing, onDirtyChange]);

  function onRemove(endpoint: AiEndpoint) {
    deleteEndpoint.mutate(endpoint.id, {
      onSuccess: () => {
        setConfirmDelete(null);
        toast.success(`Removed ${endpoint.name}`);
      },
      onError: (error) => {
        setConfirmDelete(null);
        toast.error(
          error instanceof ApiError ? error.message : "Couldn't remove that AI endpoint.",
        );
      },
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>AI endpoints</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Every place this app can send a model request — the machine under your desk, a hosted
          provider, or both. Name them here, then use them in configurations in the Lab.
        </p>

        {endpoints.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : endpoints.isError ? (
          <ErrorState
            message={
              endpoints.error instanceof ApiError
                ? endpoints.error.message
                : "Couldn't load AI endpoints."
            }
            onRetry={() => endpoints.refetch()}
          />
        ) : (
          <div className="space-y-2">
            {endpoints.data?.length === 0 && editing === undefined && (
              <p className="text-sm text-muted-foreground">
                No endpoints yet. Add one to let the app read documents.
              </p>
            )}
            {endpoints.data?.map((endpoint) =>
              editing === endpoint.id ? (
                <EndpointForm
                  key={endpoint.id}
                  endpoint={endpoint}
                  onDone={() => setEditing(undefined)}
                />
              ) : (
                <div
                  key={endpoint.id}
                  className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-border p-3"
                >
                  <div className="min-w-0 space-y-1">
                    <p className="font-medium">{endpoint.name}</p>
                    <p className="break-all font-mono text-sm text-muted-foreground">
                      {endpoint.base_url}
                    </p>
                    <div className="flex flex-wrap gap-1">
                      {endpoint.api_key_set && <Badge variant="secondary">Key saved</Badge>}
                      {endpoint.context_limit !== null && (
                        <Badge variant="secondary">
                          {endpoint.context_limit.toLocaleString()} tokens context
                        </Badge>
                      )}
                      {endpoint.model_prices.map((price) => (
                        <Badge key={price.model_name} variant="secondary" className="break-all">
                          {priceLabel(price)}
                        </Badge>
                      ))}
                      {endpoint.used_by.map((name) => (
                        <Badge key={name} variant="outline">
                          {name}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Edit ${endpoint.name}`}
                      onClick={() => setEditing(endpoint.id)}
                    >
                      <Pencil aria-hidden="true" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      aria-label={`Remove ${endpoint.name}`}
                      onClick={() => setConfirmDelete(endpoint)}
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  </div>
                </div>
              ),
            )}

            {editing === null ? (
              <EndpointForm onDone={() => setEditing(undefined)} />
            ) : (
              editing === undefined && (
                <Button type="button" variant="outline" size="sm" onClick={() => setEditing(null)}>
                  <Plus aria-hidden="true" />
                  Add endpoint
                </Button>
              )
            )}
          </div>
        )}
      </CardContent>

      <Dialog
        open={confirmDelete !== null}
        onOpenChange={(open) => !open && setConfirmDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {confirmDelete?.name}?</DialogTitle>
            <DialogDescription>
              Its saved API key goes with it. A configuration still using it must be changed first.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteEndpoint.isPending}
              onClick={() => confirmDelete && onRemove(confirmDelete)}
            >
              {deleteEndpoint.isPending && <Loader2 className="animate-spin" />}
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

/** Add or edit one endpoint. Mounted fresh per endpoint (and unmounted on save/cancel), so
 * `defaultValues` is enough -- there is no long-lived form to keep in sync with the server.
 *
 * CLAUDE.md rule 5: the key is write-only. It is never returned, so the field always starts
 * empty and an empty field on save means "leave the stored one alone", never "clear it". */
function EndpointForm({ endpoint, onDone }: { endpoint?: AiEndpoint; onDone: () => void }) {
  const create = useCreateAiEndpoint();
  const update = useUpdateAiEndpoint();
  const listModels = useListAiModels();
  const {
    register,
    control,
    handleSubmit,
    getValues,
    formState: { errors },
  } = useForm<EndpointFormValues>({
    resolver: zodResolver(endpointFormSchema),
    defaultValues: {
      name: endpoint?.name ?? "",
      base_url: endpoint?.base_url ?? "",
      api_key: "",
      timeout_seconds: String(endpoint?.timeout_seconds ?? 120),
      context_limit: endpoint?.context_limit?.toString() ?? "",
      json_mode: endpoint?.json_mode ?? "auto",
      model_prices: (endpoint?.model_prices ?? []).map((price) => ({
        model_name: price.model_name,
        price_input_per_million: price.price_input_per_million?.toString() ?? "",
        price_output_per_million: price.price_output_per_million?.toString() ?? "",
      })),
    },
  });
  const prices = useFieldArray({ control, name: "model_prices" });
  const isSaving = create.isPending || update.isPending;

  // The form's own address and key, so a connection not saved yet can list its models too.
  function modelsRequest() {
    const key = getValues("api_key").trim();
    return {
      base_url: getValues("base_url").trim(),
      ...(key ? { api_key: key } : {}),
      ...(endpoint ? { endpoint_id: endpoint.id } : {}),
    };
  }

  /** Opening a price row's model picker asks the connection for its models, once: the list
   * is kept until Test asks again. */
  function loadModels() {
    if (listModels.data || listModels.isPending || !getValues("base_url").trim()) return;
    listModels.mutate(modelsRequest());
  }

  function onTest() {
    listModels.mutate(modelsRequest(), {
      onSuccess: ({ models }) => {
        toast.success(
          models.length > 0
            ? `Endpoint reachable — ${models.length} model${models.length === 1 ? "" : "s"} available`
            : "Endpoint reachable, but it listed no models",
        );
      },
      onError: (error) => {
        toast.error(error instanceof ApiError ? error.message : "Couldn't reach the AI endpoint.");
      },
    });
  }

  function onSubmit(values: EndpointFormValues) {
    const key = values.api_key.trim();
    const body = {
      name: values.name.trim(),
      base_url: values.base_url.trim(),
      timeout_seconds: Number(values.timeout_seconds),
      context_limit: optionalNumber(values.context_limit),
      json_mode: values.json_mode,
      model_prices: values.model_prices.map((price) => ({
        model_name: price.model_name.trim(),
        price_input_per_million: optionalNumber(price.price_input_per_million),
        price_output_per_million: optionalNumber(price.price_output_per_million),
      })),
      ...(key ? { api_key: key } : {}),
    };
    const handlers = {
      onSuccess: () => {
        toast.success(endpoint ? `Saved ${body.name}` : `Added ${body.name}`);
        onDone();
      },
      onError: (error: unknown) => {
        toast.error(error instanceof ApiError ? error.message : "Couldn't save that AI endpoint.");
      },
    };

    if (endpoint) {
      update.mutate({ id: endpoint.id, ...body }, handlers);
    } else {
      create.mutate(body, handlers);
    }
  }

  const nameId = `endpoint-name-${endpoint?.id ?? "new"}`;
  const urlId = `endpoint-url-${endpoint?.id ?? "new"}`;
  const keyId = `endpoint-key-${endpoint?.id ?? "new"}`;
  const timeoutId = `endpoint-timeout-${endpoint?.id ?? "new"}`;
  const contextId = `endpoint-context-${endpoint?.id ?? "new"}`;
  const jsonModeId = `endpoint-json-${endpoint?.id ?? "new"}`;
  const pricesId = `endpoint-prices-${endpoint?.id ?? "new"}`;
  const [manualRows, setManualRows] = useState<Set<string>>(new Set());
  function toggleManual(rowId: string) {
    setManualRows((current) => {
      const next = new Set(current);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  }

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      className="space-y-4 rounded-md border border-border p-3"
    >
      <div className="space-y-1.5">
        <Label htmlFor={nameId}>Name</Label>
        <Input
          id={nameId}
          placeholder="Workshop PC"
          aria-invalid={!!errors.name}
          {...register("name")}
        />
        {errors.name && <p className="text-sm text-destructive">{errors.name.message}</p>}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={urlId}>Endpoint URL</Label>
        <Input
          id={urlId}
          className="font-mono"
          placeholder="http://192.168.1.50:11434/v1"
          aria-invalid={!!errors.base_url}
          {...register("base_url")}
        />
        {errors.base_url && <p className="text-sm text-destructive">{errors.base_url.message}</p>}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={keyId}>API key</Label>
        <Input
          id={keyId}
          type="password"
          autoComplete="off"
          placeholder={endpoint?.api_key_set ? "••••••••  (saved)" : ""}
          {...register("api_key")}
        />
        <p className="text-sm text-muted-foreground">
          {endpoint?.api_key_set
            ? "Leave blank to keep the current key."
            : "Leave blank if this endpoint needs no key."}
        </p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={timeoutId}>Reply timeout (seconds)</Label>
        <Input
          id={timeoutId}
          type="number"
          inputMode="numeric"
          className="w-32"
          aria-invalid={!!errors.timeout_seconds}
          aria-describedby={`${timeoutId}-help`}
          {...register("timeout_seconds")}
        />
        {errors.timeout_seconds ? (
          <p className="text-sm text-destructive">{errors.timeout_seconds.message}</p>
        ) : (
          <p id={`${timeoutId}-help`} className="text-sm text-muted-foreground">
            How long to wait for an answer before the call fails. A local model that loads cold and
            thinks first needs minutes; a hosted API, much less. Connecting gives up after 5 seconds
            either way.
          </p>
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={contextId}>Context limit (tokens)</Label>
        <Input
          id={contextId}
          type="number"
          inputMode="numeric"
          className="w-40"
          placeholder="Unknown"
          aria-invalid={!!errors.context_limit}
          aria-describedby={`${contextId}-help`}
          {...register("context_limit")}
        />
        {errors.context_limit ? (
          <p className="text-sm text-destructive">{errors.context_limit.message}</p>
        ) : (
          <p id={`${contextId}-help`} className="text-sm text-muted-foreground">
            Enter what the server is set to; this app cannot ask it. For Ollama that is
            OLLAMA_CONTEXT_LENGTH, 4096 by default on GPUs under 24 GB. A prompt that won't fit is
            not sent, rather than cut off silently. Leave blank to send without checking.
          </p>
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={jsonModeId}>JSON mode</Label>
        <Controller
          control={control}
          name="json_mode"
          render={({ field }) => (
            <Select value={field.value} onValueChange={field.onChange}>
              <SelectTrigger
                id={jsonModeId}
                className="w-48"
                aria-describedby={`${jsonModeId}-help`}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(JSON_MODE_LABELS) as AiJsonMode[]).map((mode) => (
                  <SelectItem key={mode} value={mode}>
                    {JSON_MODE_LABELS[mode]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        />
        <p id={`${jsonModeId}-help`} className="text-sm text-muted-foreground">
          Automatic tries a JSON schema first, then a plain JSON object, then neither, and remembers
          what the server took.
        </p>
      </div>
      <fieldset className="space-y-2" aria-describedby={`${pricesId}-help`}>
        <legend className="text-sm font-medium">Prices per million tokens</legend>
        <p id={`${pricesId}-help`} className="text-sm text-muted-foreground">
          Per model, in your provider's currency. A model without a price shows tokens only, which
          is right for one that costs nothing per token.
        </p>
        {prices.fields.length > 0 && (
          <ul className="space-y-3">
            {prices.fields.map((field, index) => {
              const rowErrors = errors.model_prices?.[index];
              const message =
                rowErrors?.model_name?.message ??
                rowErrors?.price_input_per_million?.message ??
                rowErrors?.price_output_per_million?.message;
              const rowId = `${pricesId}-${index}`;
              return (
                <li key={field.id} className="space-y-1">
                  {/* Top-aligned: the model picker has a link below it the prices do not. */}
                  <div className="flex flex-wrap items-start gap-2">
                    <div className="min-w-0 flex-1 basis-full space-y-1 sm:basis-40">
                      <Label
                        htmlFor={`${rowId}-model`}
                        className="font-normal text-muted-foreground"
                      >
                        Model
                      </Label>
                      <Controller
                        control={control}
                        name={`model_prices.${index}.model_name`}
                        render={({ field: model }) => (
                          <ModelPicker
                            id={`${rowId}-model`}
                            value={model.value}
                            onChange={model.onChange}
                            options={optionsWithCurrent(listModels.data?.models, model.value)}
                            disabled={false}
                            isPending={listModels.isPending}
                            errorMessage={
                              listModels.error
                                ? listModels.error instanceof ApiError
                                  ? listModels.error.message
                                  : "Couldn't reach this connection."
                                : null
                            }
                            onOpen={loadModels}
                            manualMode={manualRows.has(field.id)}
                            onToggleManual={() => toggleManual(field.id)}
                          />
                        )}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor={`${rowId}-in`} className="font-normal text-muted-foreground">
                        Input
                      </Label>
                      <Input
                        id={`${rowId}-in`}
                        type="number"
                        inputMode="decimal"
                        step="any"
                        className="w-24"
                        placeholder="None"
                        aria-invalid={!!rowErrors?.price_input_per_million}
                        {...register(`model_prices.${index}.price_input_per_million`)}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor={`${rowId}-out`} className="font-normal text-muted-foreground">
                        Output
                      </Label>
                      <Input
                        id={`${rowId}-out`}
                        type="number"
                        inputMode="decimal"
                        step="any"
                        className="w-24"
                        placeholder="None"
                        aria-invalid={!!rowErrors?.price_output_per_million}
                        {...register(`model_prices.${index}.price_output_per_million`)}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      // Level with the inputs, below their labels.
                      className="mt-5"
                      aria-label={`Remove price ${index + 1}`}
                      onClick={() => prices.remove(index)}
                    >
                      <X aria-hidden="true" />
                    </Button>
                  </div>
                  {message && <p className="text-sm text-destructive">{message}</p>}
                </li>
              );
            })}
          </ul>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            prices.append({
              model_name: "",
              price_input_per_million: "",
              price_output_per_million: "",
            })
          }
        >
          <Plus aria-hidden="true" />
          Add a price
        </Button>
      </fieldset>
      <div className="flex flex-wrap justify-between gap-2">
        <Button type="button" variant="outline" disabled={listModels.isPending} onClick={onTest}>
          {listModels.isPending && <Loader2 className="animate-spin" />}
          Test connection
        </Button>
        <div className="flex gap-2">
          <Button type="button" variant="outline" disabled={isSaving} onClick={onDone}>
            Cancel
          </Button>
          <Button type="submit" disabled={isSaving}>
            {isSaving && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </div>
      </div>
    </form>
  );
}
