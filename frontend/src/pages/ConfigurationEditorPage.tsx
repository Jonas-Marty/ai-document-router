import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, Loader2 } from "lucide-react";
import { useState } from "react";
import { Controller, FormProvider, useForm, useWatch } from "react-hook-form";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  builtinFilingPrompt,
  type ConfigurationFormValues,
  configurationFormSchema,
  FILING_STRATEGIES,
  RANGES,
  READING_METHODS,
  toFormValues,
  toWrite,
} from "@/components/lab/configurationSchema";
import {
  ConnectionAndModel,
  Field,
  PromptField,
  StepParameters,
} from "@/components/lab/StepFields";
import { PromptBlock } from "@/components/review/PromptDetails";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
  useBuiltinPrompts,
  useConfigurationPreview,
  useConfigurations,
  useCreateConfiguration,
  useUpdateConfiguration,
} from "@/hooks/useAiConfig";
import { useModelsByEndpoint } from "@/hooks/useModelsByEndpoint";
import { useSetNavigationGuard } from "@/hooks/useNavigationGuard";
import { ApiError } from "@/services/api/errors";
import type { AiEndpoint, BuiltinPrompts, Configuration } from "@/services/api/types";

/** SPEC 8.13: one configuration, as one form with the unsaved-changes guard. */
export default function ConfigurationEditorPage() {
  const { id } = useParams();
  const configurations = useConfigurations();
  const builtins = useBuiltinPrompts();
  const endpoints = useAiEndpoints();
  const queries = [configurations, builtins, endpoints];
  const failed = queries.find((query) => query.isError);

  let body: React.ReactNode;
  if (failed) {
    body = (
      <ErrorState
        message={failed.error instanceof ApiError ? failed.error.message : "Couldn't load this."}
        onRetry={() => {
          for (const query of queries) query.refetch();
        }}
      />
    );
  } else if (!configurations.data || !builtins.data || !endpoints.data) {
    body = (
      <div className="space-y-4" aria-busy="true">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  } else {
    const configuration = id ? configurations.data.find((row) => row.id === id) : null;
    body =
      configuration === undefined ? (
        <p className="text-sm text-muted-foreground">That configuration no longer exists.</p>
      ) : (
        <ConfigurationForm
          // A fresh form per configuration, so one's edits never carry into another's.
          key={id ?? "new"}
          configuration={configuration}
          builtins={builtins.data}
          endpoints={endpoints.data}
        />
      );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4">
      <Link
        to="/lab"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Lab
      </Link>
      <h1 className="text-lg font-semibold">{id ? "Edit configuration" : "New configuration"}</h1>
      {body}
    </div>
  );
}

function ConfigurationForm({
  configuration,
  builtins,
  endpoints,
}: {
  configuration: Configuration | null;
  builtins: BuiltinPrompts;
  endpoints: AiEndpoint[];
}) {
  const navigate = useNavigate();
  const models = useModelsByEndpoint();
  const create = useCreateConfiguration();
  const update = useUpdateConfiguration();
  const [saveError, setSaveError] = useState<string | null>(null);
  const methods = useForm<ConfigurationFormValues>({
    resolver: zodResolver(configurationFormSchema),
    mode: "onChange",
    defaultValues: toFormValues(configuration, builtins),
  });
  const {
    control,
    register,
    handleSubmit,
    getValues,
    setValue,
    formState: { errors, isDirty },
  } = methods;
  const [readingMethod, strategy] = useWatch({
    control,
    name: ["reading_method", "filing_strategy"],
  });
  const isSaving = create.isPending || update.isPending;

  useSetNavigationGuard(isDirty ? "This configuration has unsaved changes. Leave anyway?" : null);

  function onSubmit(values: ConfigurationFormValues) {
    setSaveError(null);
    const body = toWrite(values, builtins);
    const options = {
      onSuccess: (saved: Configuration) => {
        methods.reset(toFormValues(saved, builtins));
        toast.success(`Saved ${saved.name}`);
        if (!configuration) navigate(`/lab/configurations/${saved.id}`, { replace: true });
      },
      onError: (error: Error) => {
        const message =
          error instanceof ApiError ? error.message : "Couldn't save the configuration.";
        setSaveError(message);
        toast.error(message);
      },
    };
    if (configuration) {
      update.mutate({ id: configuration.id, ...body }, options);
    } else {
      create.mutate(body, options);
    }
  }

  return (
    <FormProvider {...methods}>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Card>
          <CardContent>
            <Field id="name" label="Name" error={errors.name}>
              <Input id="name" aria-invalid={!!errors.name} {...register("name")} />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Reading</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field
              id="reading-method"
              label="Method"
              help={
                readingMethod === "vision"
                  ? "A vision model reads the pages, as images, into markdown."
                  : readingMethod === "tesseract"
                    ? "Tesseract reads the rendered pages. No model."
                    : "The PDF's own text, or the searchable copy's when OCR made one. No model."
              }
            >
              <Controller
                control={control}
                name="reading_method"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger id="reading-method" className="w-full sm:w-64">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {READING_METHODS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </Field>
            {readingMethod === "vision" && (
              <>
                <ConnectionAndModel step="reading" endpoints={endpoints} models={models} />
                <PromptField
                  id="reading-system-prompt"
                  label="System prompt"
                  name="reading.system_prompt"
                  builtin={builtins.reading}
                />
                <StepParameters
                  step="reading"
                  instructionsHelp="Added to every page batch, e.g. the language your documents are in."
                />
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Filing</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <ConnectionAndModel step="filing" endpoints={endpoints} models={models} />
            <Field
              id="filing-strategy"
              label="Strategy"
              help={
                strategy === "two_calls"
                  ? "First the name's fields, then the folder given those fields. Easier on a small model."
                  : "The name's fields and the folder in one reply."
              }
            >
              <Controller
                control={control}
                name="filing_strategy"
                render={({ field }) => (
                  <Select
                    value={field.value}
                    onValueChange={(value) => {
                      const next = value as ConfigurationFormValues["filing_strategy"];
                      // An unedited prompt follows the strategy; an edited one is kept.
                      const prompt = getValues("filing.system_prompt");
                      if (prompt.trim() === builtinFilingPrompt(builtins, field.value).trim()) {
                        setValue("filing.system_prompt", builtinFilingPrompt(builtins, next), {
                          shouldDirty: true,
                        });
                      }
                      field.onChange(next);
                    }}
                  >
                    <SelectTrigger id="filing-strategy" className="w-full sm:w-64">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {FILING_STRATEGIES.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </Field>
            <PromptField
              id="filing-system-prompt"
              label={strategy === "two_calls" ? "Fields prompt (first call)" : "System prompt"}
              name="filing.system_prompt"
              builtin={builtinFilingPrompt(builtins, strategy)}
            />
            {strategy === "two_calls" && (
              <PromptField
                id="folder-system-prompt"
                label="Folder prompt (second call)"
                name="folder_system_prompt"
                builtin={builtins.filing_folder}
              />
            )}
            <StepParameters
              step="filing"
              instructionsHelp="Your own filing rules, e.g. where insurance letters go."
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Context</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-3">
            <Field
              id="max-examples"
              label="Examples"
              error={errors.max_examples}
              help={`Documents filed before, shown with their approved fields and folder. Default 8; ${RANGES.max_examples.join("–")}.`}
            >
              <Input
                id="max-examples"
                type="number"
                inputMode="numeric"
                aria-invalid={!!errors.max_examples}
                {...register("max_examples")}
              />
            </Field>
            <Field
              id="max-folders"
              label="Folders listed"
              error={errors.max_folders}
              help="Shallower folders are kept first. Default 250."
            >
              <Input
                id="max-folders"
                type="number"
                inputMode="numeric"
                aria-invalid={!!errors.max_folders}
                {...register("max_folders")}
              />
            </Field>
            <Field
              id="max-text-chars"
              label="Document text (characters)"
              error={errors.max_text_chars}
              help="How much of the reading filing is given. Default 6,000."
            >
              <Input
                id="max-text-chars"
                type="number"
                inputMode="numeric"
                aria-invalid={!!errors.max_text_chars}
                {...register("max_text_chars")}
              />
            </Field>
          </CardContent>
        </Card>

        {configuration && <Preview configurationId={configuration.id} />}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
          {saveError && (
            <p className="text-sm text-destructive sm:mr-auto" role="alert">
              {saveError}
            </p>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={!isDirty || isSaving}
            onClick={() => methods.reset()}
          >
            Discard changes
          </Button>
          <Button type="submit" disabled={(!isDirty && configuration !== null) || isSaving}>
            {isSaving && <Loader2 className="animate-spin" aria-hidden="true" />}
            {configuration ? "Save" : "Create configuration"}
          </Button>
        </div>
      </form>
    </FormProvider>
  );
}

/** What each call is sent, fetched only once opened: building it walks WebDAV. */
function Preview({ configurationId }: { configurationId: string }) {
  const [open, setOpen] = useState(false);
  const preview = useConfigurationPreview(configurationId, open);

  return (
    <Card>
      <CardHeader>
        <CardTitle>What the models are given</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto p-0"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Hide" : "Show each call, with the document as a placeholder"}
        </Button>
        {open && (
          <>
            <p className="text-sm text-muted-foreground">
              Built from the saved configuration — save first to see your edits here.
            </p>
            {preview.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : preview.isError ? (
              <ErrorState
                message={
                  preview.error instanceof ApiError
                    ? preview.error.message
                    : "Couldn't build the preview."
                }
                onRetry={() => preview.refetch()}
              />
            ) : (
              preview.data?.map((call) => (
                <section key={call.step} className="space-y-2 border-t border-border pt-3">
                  <h3 className="text-sm font-medium">
                    {call.step}
                    {call.model_label && (
                      <span className="ml-2 font-mono text-xs text-muted-foreground">
                        {call.model_label}
                      </span>
                    )}
                  </h3>
                  <p className="text-sm text-muted-foreground">{call.note}</p>
                  {call.system_prompt && (
                    <PromptBlock label="System prompt" text={call.system_prompt} />
                  )}
                  {call.user_prompt && <PromptBlock label="Message" text={call.user_prompt} />}
                </section>
              ))
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
