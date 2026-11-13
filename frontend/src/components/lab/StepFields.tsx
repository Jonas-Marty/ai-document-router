import { type ReactNode, useState } from "react";
import { Controller, type FieldError, useFormContext, useWatch } from "react-hook-form";
import { ModelPicker, optionsWithCurrent } from "@/components/settings/ModelPicker";
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
import { Textarea } from "@/components/ui/textarea";
import type { ModelsByEndpoint } from "@/hooks/useModelsByEndpoint";
import { cn } from "@/lib/utils";
import type { AiEndpoint } from "@/services/api/types";
import {
  type ConfigurationFormValues,
  MAX_INSTRUCTIONS_CHARS,
  RANGES,
  THINKING,
} from "./configurationSchema";

type Step = "reading" | "filing";

/** A step's connection and the model on it (SPEC 8.7's model dropdown). */
export function ConnectionAndModel({
  step,
  endpoints,
  models,
}: {
  step: Step;
  endpoints: AiEndpoint[];
  models: ModelsByEndpoint;
}) {
  const { control, setValue, formState } = useFormContext<ConfigurationFormValues>();
  const [manual, setManual] = useState(false);
  const endpointId = useWatch({ control, name: `${step}.endpoint_id` });
  const endpoint = endpoints.find((candidate) => candidate.id === endpointId);
  const errors = formState.errors[step];

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field id={`${step}-endpoint`} label="Connection" error={errors?.endpoint_id}>
        <Controller
          control={control}
          name={`${step}.endpoint_id`}
          render={({ field }) => (
            <Select
              value={field.value}
              onValueChange={(value) => {
                field.onChange(value);
                // A model name belongs to the connection it was listed on.
                setValue(`${step}.model_name`, "", { shouldDirty: true, shouldValidate: true });
              }}
            >
              <SelectTrigger
                id={`${step}-endpoint`}
                className="w-full"
                aria-invalid={!!errors?.endpoint_id}
              >
                <SelectValue placeholder="Choose a connection" />
              </SelectTrigger>
              <SelectContent>
                {endpoints.map((candidate) => (
                  <SelectItem key={candidate.id} value={candidate.id}>
                    {candidate.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        />
      </Field>
      <Field id={`${step}-model`} label="Model" error={errors?.model_name}>
        <Controller
          control={control}
          name={`${step}.model_name`}
          render={({ field }) => (
            <ModelPicker
              id={`${step}-model`}
              value={field.value}
              onChange={field.onChange}
              options={optionsWithCurrent(
                endpoint ? models.byEndpoint[endpoint.id] : undefined,
                field.value,
              )}
              disabled={endpoint === undefined}
              isPending={endpoint !== undefined && models.loadingId === endpoint.id}
              errorMessage={endpoint ? (models.errors[endpoint.id] ?? null) : null}
              onOpen={() => endpoint && models.load(endpoint)}
              manualMode={manual}
              onToggleManual={() => setManual((current) => !current)}
            />
          )}
        />
      </Field>
    </div>
  );
}

/** A system prompt shown as the text actually sent: the built-in one until edited. */
export function PromptField({
  id,
  label,
  name,
  builtin,
}: {
  id: string;
  label: string;
  name: "reading.system_prompt" | "filing.system_prompt" | "folder_system_prompt";
  builtin: string;
}) {
  const { control, register, setValue, getFieldState, formState } =
    useFormContext<ConfigurationFormValues>();
  const value = useWatch({ control, name });
  const isBuiltin = value.trim() === builtin.trim();
  const error = getFieldState(name, formState).error;

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <Label htmlFor={id}>{label}</Label>
        {isBuiltin ? (
          <span className="text-xs text-muted-foreground">Built-in</span>
        ) : (
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto p-0"
            onClick={() => setValue(name, builtin, { shouldDirty: true, shouldValidate: true })}
          >
            Use the built-in prompt
          </Button>
        )}
      </div>
      <Textarea
        id={id}
        rows={8}
        className="max-h-96 font-mono text-xs"
        aria-invalid={!!error}
        {...register(name)}
      />
      {error && <p className="text-sm text-destructive">{error.message}</p>}
    </div>
  );
}

/** Instructions and the call parameters of one model step. */
export function StepParameters({
  step,
  instructionsHelp,
}: {
  step: Step;
  instructionsHelp: string;
}) {
  const { control, register, formState } = useFormContext<ConfigurationFormValues>();
  const instructions = useWatch({ control, name: `${step}.instructions` });
  const errors = formState.errors[step];
  const length = instructions.trim().length;

  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor={`${step}-instructions`}>Instructions</Label>
        <Textarea
          id={`${step}-instructions`}
          rows={3}
          className="max-h-64"
          aria-invalid={!!errors?.instructions}
          {...register(`${step}.instructions`)}
        />
        <div className="flex items-start justify-between gap-3 text-sm">
          {errors?.instructions ? (
            <p className="text-destructive">{errors.instructions.message}</p>
          ) : (
            <p className="text-muted-foreground">{instructionsHelp}</p>
          )}
          <span
            className={cn(
              "shrink-0 tabular-nums",
              length > MAX_INSTRUCTIONS_CHARS ? "text-destructive" : "text-muted-foreground",
            )}
          >
            {length} / {MAX_INSTRUCTIONS_CHARS}
          </span>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field
          id={`${step}-temperature`}
          label="Temperature"
          error={errors?.temperature}
          help={`${RANGES.temperature[0]}–${RANGES.temperature[1]}; low copies, high invents.`}
        >
          <Input
            id={`${step}-temperature`}
            type="number"
            inputMode="decimal"
            step="0.05"
            aria-invalid={!!errors?.temperature}
            {...register(`${step}.temperature`)}
          />
        </Field>
        <Field
          id={`${step}-max-tokens`}
          label="Output limit (tokens)"
          error={errors?.max_tokens}
          help="A reply that reaches it is cut off and fails."
        >
          <Input
            id={`${step}-max-tokens`}
            type="number"
            inputMode="numeric"
            aria-invalid={!!errors?.max_tokens}
            {...register(`${step}.max_tokens`)}
          />
        </Field>
        <Field
          id={`${step}-thinking`}
          label="Thinking"
          help="Off asks Ollama to skip thinking where the model allows it."
        >
          <Controller
            control={control}
            name={`${step}.thinking`}
            render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger id={`${step}-thinking`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {THINKING.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          />
        </Field>
      </div>
    </>
  );
}

/** Label, the control, and an error or help line under it. */
export function Field({
  id,
  label,
  error,
  help,
  children,
}: {
  id: string;
  label: string;
  error?: FieldError;
  help?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0 space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? (
        <p className="text-sm text-destructive">{error.message}</p>
      ) : (
        help && <p className="text-sm text-muted-foreground">{help}</p>
      )}
    </div>
  );
}
