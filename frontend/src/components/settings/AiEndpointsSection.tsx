import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
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
import type { AiEndpoint, AiJsonMode } from "@/services/api/types";
import { type EndpointFormValues, endpointFormSchema, optionalNumber } from "./settingsSchemas";

const TASK_LABELS: Record<string, string> = {
  extraction: "Extraction",
  filing: "Filing",
};

const JSON_MODE_LABELS: Record<AiJsonMode, string> = {
  auto: "Automatic",
  json_schema: "JSON schema",
  json_object: "JSON object",
  none: "None",
};

/** "0.4 in · 1.6 out per 1M tokens", or null when neither price is set. */
function priceLabel(endpoint: AiEndpoint): string | null {
  const { price_input_per_million: input, price_output_per_million: output } = endpoint;
  if (input === null && output === null) return null;
  return `${input ?? 0} in · ${output ?? 0} out per 1M tokens`;
}

/** SPEC 8.7: the places the app can send a model request, each with a name of its own.
 *
 * Not a SectionCard: an endpoint is its own resource with its own create/update/delete, so
 * each one saves when *it* is saved rather than sharing a single Save button with the rest of
 * the section. What tasks do with these endpoints is the next card down (AiTasksSection). */
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
          provider, or both. Name them here, then assign them to tasks below.
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
                      {priceLabel(endpoint) && (
                        <Badge variant="secondary">{priceLabel(endpoint)}</Badge>
                      )}
                      {endpoint.used_by.map((task) => (
                        <Badge key={task} variant="outline">
                          {TASK_LABELS[task] ?? task}
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
              Its saved API key goes with it. Any task still using it must be changed first.
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
      price_input_per_million: endpoint?.price_input_per_million?.toString() ?? "",
      price_output_per_million: endpoint?.price_output_per_million?.toString() ?? "",
    },
  });
  const isSaving = create.isPending || update.isPending;

  function onTest() {
    const key = getValues("api_key").trim();
    listModels.mutate(
      {
        base_url: getValues("base_url").trim(),
        ...(key ? { api_key: key } : {}),
        ...(endpoint ? { endpoint_id: endpoint.id } : {}),
      },
      {
        onSuccess: ({ models }) => {
          toast.success(
            models.length > 0
              ? `Endpoint reachable — ${models.length} model${models.length === 1 ? "" : "s"} available`
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

  function onSubmit(values: EndpointFormValues) {
    const key = values.api_key.trim();
    const body = {
      name: values.name.trim(),
      base_url: values.base_url.trim(),
      timeout_seconds: Number(values.timeout_seconds),
      context_limit: optionalNumber(values.context_limit),
      json_mode: values.json_mode,
      price_input_per_million: optionalNumber(values.price_input_per_million),
      price_output_per_million: optionalNumber(values.price_output_per_million),
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
  const priceInId = `endpoint-price-in-${endpoint?.id ?? "new"}`;
  const priceOutId = `endpoint-price-out-${endpoint?.id ?? "new"}`;

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
            How long to wait for an answer before trying the next endpoint. A local model that loads
            cold and thinks first needs minutes; a hosted API, much less. Connecting gives up after
            5 seconds either way.
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
      <fieldset className="space-y-1.5">
        <legend className="text-sm font-medium">Price per million tokens</legend>
        <div className="flex flex-wrap gap-3">
          <div className="space-y-1">
            <Label htmlFor={priceInId} className="font-normal text-muted-foreground">
              Input
            </Label>
            <Input
              id={priceInId}
              type="number"
              inputMode="decimal"
              step="any"
              className="w-32"
              placeholder="None"
              aria-invalid={!!errors.price_input_per_million}
              {...register("price_input_per_million")}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={priceOutId} className="font-normal text-muted-foreground">
              Output
            </Label>
            <Input
              id={priceOutId}
              type="number"
              inputMode="decimal"
              step="any"
              className="w-32"
              placeholder="None"
              aria-invalid={!!errors.price_output_per_million}
              {...register("price_output_per_million")}
            />
          </div>
        </div>
        {(errors.price_input_per_million || errors.price_output_per_million) && (
          <p className="text-sm text-destructive">
            {(errors.price_input_per_million ?? errors.price_output_per_million)?.message}
          </p>
        )}
        <p className="text-sm text-muted-foreground">
          In your provider's currency. Leave blank for a connection that costs nothing per token;
          usage then shows tokens only.
        </p>
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
