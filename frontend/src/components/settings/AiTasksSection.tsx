import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { toast } from "sonner";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAiEndpoints, useAiTasks, useUpdateAiTask } from "@/hooks/useAiConfig";
import { useListAiModels } from "@/hooks/useSettings";
import { ApiError } from "@/services/api/errors";
import type { AiEndpoint, AiTask, AiTaskChain } from "@/services/api/types";
import { ModelPicker, optionsWithCurrent } from "./ModelPicker";
import { SectionCard } from "./SectionCard";
import { type ChainFormValues, chainFormSchema } from "./settingsSchemas";

const TASKS: Record<AiTask, { title: string; blurb: string; empty: string }> = {
  extraction: {
    title: "Extraction — read the pages",
    blurb:
      "Renders each page to an image and asks a model that can see to transcribe it as markdown, so headings, tables and line items survive instead of collapsing into one blob of text. Needs a vision model (GOT-OCR 2.0, Qwen-VL, and similar).",
    empty: "No endpoint assigned — documents are read from the PDF's own text layer instead.",
  },
  filing: {
    title: "Filing — choose the filename",
    blurb:
      "Reads what came out of extraction and proposes the filename, folder and document date. A text model is enough.",
    empty: "No endpoint assigned — nothing will be proposed until one is.",
  },
};

/** SPEC 6.3a's workflow, one card per task. Each task is an ordered chain: the first endpoint
 * that answers wins, and the ones under it exist for the days it doesn't -- a laptop that is
 * off, an Ollama that was never started. Saved per task, because reordering filing has nothing
 * to do with reordering extraction. */
export function AiTasksSection({ onDirtyChange }: { onDirtyChange: (dirty: boolean) => void }) {
  const endpoints = useAiEndpoints();
  const tasks = useAiTasks();
  const models = useModelsByEndpoint();
  const [dirty, setDirty] = useState<Partial<Record<AiTask, boolean>>>({});

  const onTaskDirtyChange = useCallback(
    (task: AiTask, isDirty: boolean) =>
      setDirty((current) =>
        current[task] === isDirty ? current : { ...current, [task]: isDirty },
      ),
    [],
  );

  const anyDirty = Object.values(dirty).some(Boolean);
  useEffect(() => {
    onDirtyChange(anyDirty);
  }, [anyDirty, onDirtyChange]);

  if (endpoints.isLoading || tasks.isLoading) {
    return <Skeleton className="h-40 w-full" />;
  }
  if (endpoints.isError || tasks.isError) {
    const error = endpoints.error ?? tasks.error;
    return (
      <ErrorState
        message={error instanceof ApiError ? error.message : "Couldn't load the AI workflow."}
        onRetry={() => {
          endpoints.refetch();
          tasks.refetch();
        }}
      />
    );
  }

  return (
    <>
      {(tasks.data ?? []).map((chain) => (
        <TaskChainForm
          key={chain.task}
          chain={chain}
          endpoints={endpoints.data ?? []}
          models={models}
          onDirtyChange={onTaskDirtyChange}
        />
      ))}
    </>
  );
}

function TaskChainForm({
  chain,
  endpoints,
  models,
  onDirtyChange,
}: {
  chain: AiTaskChain;
  endpoints: AiEndpoint[];
  models: ModelsByEndpoint;
  onDirtyChange: (task: AiTask, dirty: boolean) => void;
}) {
  const updateTask = useUpdateAiTask();
  // Keyed by useFieldArray's row id, not index: rows move and are removed, and an index-based
  // set would silently move one row's manual toggle onto another.
  const [manualRows, setManualRows] = useState<Set<string>>(new Set());
  // See FoldersSection's comment: `values` must stay reference-stable across incidental
  // re-renders, not a fresh object every time.
  const values = useMemo(
    () => ({
      steps: chain.steps.map((step) => ({
        endpoint_id: step.endpoint_id,
        model_name: step.model_name,
      })),
    }),
    [chain],
  );
  const methods = useForm<ChainFormValues>({
    resolver: zodResolver(chainFormSchema),
    values,
    resetOptions: { keepDirtyValues: true },
  });
  const {
    control,
    handleSubmit,
    watch,
    formState: { errors, isDirty },
  } = methods;
  const steps = useFieldArray({ control, name: "steps" });
  const watched = watch("steps");
  const copy = TASKS[chain.task];

  // Reordering and removing are the whole point of a chain, and neither goes through an
  // <input>, so useFieldArray's own mutators have to mark the form dirty themselves.
  const touch = () => methods.setValue("steps", methods.getValues("steps"), { shouldDirty: true });

  useEffect(() => {
    onDirtyChange(chain.task, isDirty);
  }, [chain.task, isDirty, onDirtyChange]);

  function onSubmit(formValues: ChainFormValues) {
    updateTask.mutate(
      {
        task: chain.task,
        steps: formValues.steps.map((step) => ({
          endpoint_id: step.endpoint_id,
          model_name: step.model_name.trim(),
        })),
      },
      {
        onSuccess: (saved) => {
          methods.reset(
            {
              steps: saved.steps.map((step) => ({
                endpoint_id: step.endpoint_id,
                model_name: step.model_name,
              })),
            },
            { keepDirtyValues: false },
          );
          toast.success("AI workflow saved");
        },
        onError: (error) => {
          toast.error(error instanceof ApiError ? error.message : "Couldn't save the AI workflow.");
        },
      },
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <SectionCard
        title={copy.title}
        isDirty={isDirty}
        isSaving={updateTask.isPending}
        onDiscard={() => methods.reset(values, { keepDirtyValues: false })}
      >
        <p className="text-sm text-muted-foreground">{copy.blurb}</p>

        {steps.fields.length === 0 ? (
          <p className="text-sm text-muted-foreground">{copy.empty}</p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Tried top to bottom. The first endpoint that answers is used; the rest are there for
            when it can't be reached.
          </p>
        )}

        <div className="space-y-3">
          {steps.fields.map((field, index) => {
            const selectedId = watched?.[index]?.endpoint_id ?? "";
            const endpoint = endpoints.find((candidate) => candidate.id === selectedId);
            const modelName = watched?.[index]?.model_name ?? "";
            const stepErrors = errors.steps?.[index];

            return (
              <div key={field.id} className="space-y-3 rounded-md border border-border p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">
                    {index === 0 ? "First choice" : `Fallback ${index}`}
                  </span>
                  <div className="flex gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Move step ${index + 1} up`}
                      disabled={index === 0}
                      onClick={() => {
                        steps.move(index, index - 1);
                        touch();
                      }}
                    >
                      <ArrowUp aria-hidden="true" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Move step ${index + 1} down`}
                      disabled={index === steps.fields.length - 1}
                      onClick={() => {
                        steps.move(index, index + 1);
                        touch();
                      }}
                    >
                      <ArrowDown aria-hidden="true" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Remove step ${index + 1}`}
                      onClick={() => {
                        steps.remove(index);
                        touch();
                      }}
                    >
                      <X aria-hidden="true" />
                    </Button>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor={`${chain.task}-endpoint-${field.id}`}>Endpoint</Label>
                  <Controller
                    control={control}
                    name={`steps.${index}.endpoint_id`}
                    render={({ field: controlled }) => (
                      <Select value={controlled.value} onValueChange={controlled.onChange}>
                        <SelectTrigger id={`${chain.task}-endpoint-${field.id}`} className="w-full">
                          <SelectValue placeholder="Choose an endpoint" />
                        </SelectTrigger>
                        <SelectContent>
                          {endpoints.length === 0 ? (
                            <p className="px-2 py-1.5 text-sm text-muted-foreground">
                              Add an endpoint above first.
                            </p>
                          ) : (
                            endpoints.map((candidate) => (
                              <SelectItem key={candidate.id} value={candidate.id}>
                                {candidate.name}
                              </SelectItem>
                            ))
                          )}
                        </SelectContent>
                      </Select>
                    )}
                  />
                  {stepErrors?.endpoint_id && (
                    <p className="text-sm text-destructive">{stepErrors.endpoint_id.message}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor={`${chain.task}-model-${field.id}`}>Model</Label>
                  <Controller
                    control={control}
                    name={`steps.${index}.model_name`}
                    render={({ field: controlled }) => (
                      <ModelPicker
                        id={`${chain.task}-model-${field.id}`}
                        value={controlled.value}
                        onChange={controlled.onChange}
                        options={optionsWithCurrent(
                          endpoint ? models.byEndpoint[endpoint.id] : undefined,
                          modelName,
                        )}
                        disabled={endpoint === undefined}
                        isPending={endpoint !== undefined && models.loadingId === endpoint.id}
                        errorMessage={endpoint ? (models.errors[endpoint.id] ?? null) : null}
                        onOpen={() => endpoint && models.load(endpoint)}
                        manualMode={manualRows.has(field.id)}
                        onToggleManual={() =>
                          setManualRows((current) => {
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
                  {stepErrors?.model_name && (
                    <p className="text-sm text-destructive">{stepErrors.model_name.message}</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={endpoints.length === 0}
          onClick={() => {
            steps.append({ endpoint_id: "", model_name: "" });
            touch();
          }}
        >
          <Plus aria-hidden="true" />
          {steps.fields.length === 0 ? "Add endpoint" : "Add fallback"}
        </Button>
      </SectionCard>
    </form>
  );
}

interface ModelsByEndpoint {
  byEndpoint: Record<string, string[]>;
  errors: Record<string, string>;
  loadingId: string | null;
  load: (endpoint: AiEndpoint) => void;
}

/** One model list per endpoint, fetched the first time a dropdown pointing at it is opened.
 * Shared across both task cards, so picking the same endpoint for extraction and filing costs
 * one round trip to the provider rather than one per picker. Deliberately not a query: it is
 * a live probe of somebody else's server, not state this app owns. */
function useModelsByEndpoint(): ModelsByEndpoint {
  const listModels = useListAiModels();
  const [byEndpoint, setByEndpoint] = useState<Record<string, string[]>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loadingId, setLoadingId] = useState<string | null>(null);

  const load = useCallback(
    (endpoint: AiEndpoint) => {
      if (byEndpoint[endpoint.id] !== undefined || loadingId !== null) return;
      setLoadingId(endpoint.id);
      listModels.mutate(
        { base_url: endpoint.base_url, endpoint_id: endpoint.id },
        {
          onSuccess: ({ models }) => {
            setByEndpoint((current) => ({ ...current, [endpoint.id]: models }));
            setErrors((current) => {
              const { [endpoint.id]: _removed, ...rest } = current;
              return rest;
            });
            setLoadingId(null);
          },
          onError: (error) => {
            setErrors((current) => ({
              ...current,
              [endpoint.id]:
                error instanceof ApiError ? error.message : "Couldn't reach this endpoint.",
            }));
            setLoadingId(null);
          },
        },
      );
    },
    [byEndpoint, listModels, loadingId],
  );

  return { byEndpoint, errors, loadingId, load };
}
