import { z } from "zod";
import type {
  AiThinking,
  BuiltinPrompts,
  Configuration,
  ConfigurationStep,
  ConfigurationWrite,
  FilingStrategy,
  ReadingMethod,
} from "@/services/api/types";

// Mirror the ranges in services/configurations.py, which is what actually enforces them.
export const MAX_NAME_CHARS = 80;
export const MAX_PROMPT_CHARS = 20_000;
export const MAX_INSTRUCTIONS_CHARS = 2000;
export const RANGES = {
  temperature: [0, 2],
  max_tokens: [256, 32_768],
  max_examples: [0, 20],
  max_folders: [10, 1000],
  max_text_chars: [1000, 30_000],
} as const;

// What a new configuration starts with; the backend's own defaults.
export const DEFAULTS = {
  reading_max_tokens: 4096,
  filing_max_tokens: 2048,
  temperature: 0.1,
  max_examples: 8,
  max_folders: 250,
  max_text_chars: 6000,
} as const;

export const READING_METHODS: { value: ReadingMethod; label: string }[] = [
  { value: "text_layer", label: "Text layer" },
  { value: "tesseract", label: "Tesseract" },
  { value: "vision", label: "Vision model" },
];

export const FILING_STRATEGIES: { value: FilingStrategy; label: string }[] = [
  { value: "one_call", label: "One call" },
  { value: "two_calls", label: "Two calls" },
];

export const THINKING: { value: AiThinking; label: string }[] = [
  { value: "default", label: "Model default" },
  { value: "off", label: "Off" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

function inRange(label: string, [low, high]: readonly [number, number], integer: boolean) {
  return z.string().refine(
    (value) => {
      if (value.trim() === "") return false;
      const number = Number(value);
      return (
        Number.isFinite(number) &&
        (!integer || Number.isInteger(number)) &&
        number >= low &&
        number <= high
      );
    },
    `${label} must be between ${low.toLocaleString("en")} and ${high.toLocaleString("en")}.`,
  );
}

const prompt = z
  .string()
  .refine(
    (value) => value.length <= MAX_PROMPT_CHARS,
    `Keep it to ${MAX_PROMPT_CHARS.toLocaleString("en")} characters or fewer.`,
  );

const stepSchema = z.object({
  endpoint_id: z.string(),
  model_name: z.string(),
  system_prompt: prompt,
  instructions: z
    .string()
    .refine(
      (value) => value.trim().length <= MAX_INSTRUCTIONS_CHARS,
      `Keep it to ${MAX_INSTRUCTIONS_CHARS} characters or fewer.`,
    ),
  temperature: inRange("Temperature", RANGES.temperature, false),
  max_tokens: inRange("The output limit", RANGES.max_tokens, true),
  thinking: z.enum(["default", "off", "low", "medium", "high"]),
});

export const configurationFormSchema = z
  .object({
    name: z
      .string()
      .refine((value) => value.trim().length > 0, "Give the configuration a name.")
      .refine(
        (value) => value.trim().length <= MAX_NAME_CHARS,
        `Keep it to ${MAX_NAME_CHARS} characters or fewer.`,
      ),
    reading_method: z.enum(["text_layer", "tesseract", "vision"]),
    reading: stepSchema,
    filing_strategy: z.enum(["one_call", "two_calls"]),
    filing: stepSchema,
    folder_system_prompt: prompt,
    max_examples: inRange("Examples", RANGES.max_examples, true),
    max_folders: inRange("Folders listed", RANGES.max_folders, true),
    max_text_chars: inRange("Document text", RANGES.max_text_chars, true),
  })
  .superRefine((values, context) => {
    // A step only needs a model when it calls one; text layer and Tesseract do not.
    const steps: ["reading" | "filing", boolean][] = [
      ["reading", values.reading_method === "vision"],
      ["filing", true],
    ];
    for (const [step, needsModel] of steps) {
      if (!needsModel) continue;
      if (!values[step].endpoint_id) {
        context.addIssue({
          code: "custom",
          path: [step, "endpoint_id"],
          message: "Choose a connection.",
        });
      }
      if (!values[step].model_name.trim()) {
        context.addIssue({
          code: "custom",
          path: [step, "model_name"],
          message: "Choose a model.",
        });
      }
    }
  });

export type ConfigurationFormValues = z.infer<typeof configurationFormSchema>;
export type StepFormValues = ConfigurationFormValues["reading"];

/** The built-in prompt a step is sent when it has none of its own. */
export function builtinFilingPrompt(builtins: BuiltinPrompts, strategy: FilingStrategy): string {
  return strategy === "one_call" ? builtins.filing_one_call : builtins.filing_fields;
}

function stepValues(step: ConfigurationStep | null, builtin: string, maxTokens: number) {
  return {
    endpoint_id: step?.endpoint_id ?? "",
    model_name: step?.model_name ?? "",
    // The editor shows the text actually sent: the built-in one until someone edits it.
    system_prompt: step?.system_prompt ?? builtin,
    instructions: step?.instructions ?? "",
    temperature: String(step?.temperature ?? DEFAULTS.temperature),
    max_tokens: String(step?.max_tokens ?? maxTokens),
    thinking: step?.thinking ?? "default",
  } satisfies StepFormValues;
}

export function toFormValues(
  configuration: Configuration | null,
  builtins: BuiltinPrompts,
): ConfigurationFormValues {
  const strategy = configuration?.filing_strategy ?? "one_call";
  return {
    name: configuration?.name ?? "",
    reading_method: configuration?.reading_method ?? "text_layer",
    reading: stepValues(
      configuration?.reading ?? null,
      builtins.reading,
      DEFAULTS.reading_max_tokens,
    ),
    filing_strategy: strategy,
    filing: stepValues(
      configuration?.filing ?? null,
      builtinFilingPrompt(builtins, strategy),
      DEFAULTS.filing_max_tokens,
    ),
    folder_system_prompt: configuration?.folder_system_prompt ?? builtins.filing_folder,
    max_examples: String(configuration?.max_examples ?? DEFAULTS.max_examples),
    max_folders: String(configuration?.max_folders ?? DEFAULTS.max_folders),
    max_text_chars: String(configuration?.max_text_chars ?? DEFAULTS.max_text_chars),
  };
}

/** Unchanged built-in text is sent as null, so the configuration keeps following the built-in
 * prompt as it improves rather than freezing today's copy of it. */
function ownPrompt(text: string, builtin: string): string | null {
  return text.trim() === "" || text.trim() === builtin.trim() ? null : text;
}

function toStep(values: StepFormValues, builtin: string, needsModel: boolean): ConfigurationStep {
  return {
    endpoint_id: needsModel ? values.endpoint_id : null,
    endpoint_name: null,
    model_name: needsModel ? values.model_name.trim() : null,
    system_prompt: ownPrompt(values.system_prompt, builtin),
    instructions: values.instructions.trim() || null,
    temperature: Number(values.temperature),
    max_tokens: Number(values.max_tokens),
    thinking: values.thinking,
  };
}

export function toWrite(
  values: ConfigurationFormValues,
  builtins: BuiltinPrompts,
): ConfigurationWrite {
  return {
    name: values.name.trim(),
    reading_method: values.reading_method,
    reading: toStep(values.reading, builtins.reading, values.reading_method === "vision"),
    filing_strategy: values.filing_strategy,
    filing: toStep(values.filing, builtinFilingPrompt(builtins, values.filing_strategy), true),
    folder_system_prompt: ownPrompt(values.folder_system_prompt, builtins.filing_folder),
    max_examples: Number(values.max_examples),
    max_folders: Number(values.max_folders),
    max_text_chars: Number(values.max_text_chars),
  };
}

function stepLabel(step: ConfigurationStep): string {
  return [step.endpoint_name ?? "Missing connection", step.model_name].filter(Boolean).join(" · ");
}

/** "Text layer", "Tesseract", or "Tower · qwen2.5vl" (SPEC 8.13). */
export function describeReading(configuration: Configuration): string {
  if (configuration.reading_method === "vision") return stepLabel(configuration.reading);
  return (
    READING_METHODS.find((method) => method.value === configuration.reading_method)?.label ?? ""
  );
}

/** "Tower · qwen3:8b · two calls" (SPEC 8.13). */
export function describeFiling(configuration: Configuration): string {
  const strategy = configuration.filing_strategy === "two_calls" ? "two calls" : "one call";
  return `${stepLabel(configuration.filing)} · ${strategy}`;
}
