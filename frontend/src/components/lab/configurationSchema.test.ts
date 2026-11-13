import { describe, expect, it } from "vitest";
import type { BuiltinPrompts, Configuration } from "@/services/api/types";
import {
  configurationFormSchema,
  describeFiling,
  describeReading,
  toFormValues,
  toWrite,
} from "./configurationSchema";

const BUILTINS: BuiltinPrompts = {
  reading: "Read the pages.",
  filing_one_call: "Name and file it.",
  filing_fields: "Name it.",
  filing_folder: "File it.",
};

function configuration(overrides: Partial<Configuration> = {}): Configuration {
  const step = {
    endpoint_id: null,
    endpoint_name: null,
    model_name: null,
    system_prompt: null,
    instructions: null,
    temperature: 0.1,
    max_tokens: 4096,
    thinking: "default" as const,
  };
  return {
    id: "c1",
    name: "Daily",
    is_default: true,
    reading_method: "text_layer",
    reading: step,
    filing_strategy: "one_call",
    filing: {
      ...step,
      endpoint_id: "ep1",
      endpoint_name: "Tower",
      model_name: "qwen3:8b",
      max_tokens: 2048,
    },
    folder_system_prompt: null,
    max_examples: 8,
    max_folders: 250,
    max_text_chars: 6000,
    ...overrides,
  };
}

describe("toFormValues and toWrite", () => {
  it("shows the built-in prompts and sends them back as null when untouched", () => {
    const values = toFormValues(configuration(), BUILTINS);

    expect(values.filing.system_prompt).toBe("Name and file it.");
    expect(values.folder_system_prompt).toBe("File it.");

    const written = toWrite(values, BUILTINS);
    expect(written.filing.system_prompt).toBeNull();
    expect(written.reading.system_prompt).toBeNull();
    expect(written.folder_system_prompt).toBeNull();
  });

  it("keeps an edited prompt, and sends blank instructions as null", () => {
    const values = toFormValues(configuration(), BUILTINS);
    values.filing.system_prompt = "My own prompt.";
    values.filing.instructions = "   ";

    const written = toWrite(values, BUILTINS);

    expect(written.filing.system_prompt).toBe("My own prompt.");
    expect(written.filing.instructions).toBeNull();
  });

  it("drops the reading connection when the method needs no model", () => {
    const values = toFormValues(configuration(), BUILTINS);
    values.reading.endpoint_id = "ep1";
    values.reading.model_name = "v";

    const written = toWrite(values, BUILTINS);

    expect(written.reading.endpoint_id).toBeNull();
    expect(written.reading.model_name).toBeNull();
  });

  it("sends numbers as numbers", () => {
    const written = toWrite(toFormValues(configuration(), BUILTINS), BUILTINS);

    expect(written.filing.temperature).toBe(0.1);
    expect(written.max_text_chars).toBe(6000);
  });
});

describe("configurationFormSchema", () => {
  const valid = () => toFormValues(configuration(), BUILTINS);

  it("accepts a complete configuration", () => {
    expect(configurationFormSchema.safeParse(valid()).success).toBe(true);
  });

  it("asks for a reading connection and model only for a vision model", () => {
    const values = { ...valid(), reading_method: "vision" as const };
    const result = configurationFormSchema.safeParse(values);

    expect(result.success).toBe(false);
    const paths = result.error?.issues.map((issue) => issue.path.join("."));
    expect(paths).toEqual(["reading.endpoint_id", "reading.model_name"]);
  });

  it("always asks for a filing model", () => {
    const values = valid();
    values.filing.model_name = "  ";

    const result = configurationFormSchema.safeParse(values);

    expect(result.error?.issues[0]?.message).toBe("Choose a model.");
  });

  it.each([
    ["max_text_chars", "999", "Document text must be between 1,000 and 30,000."],
    ["max_folders", "5", "Folders listed must be between 10 and 1,000."],
    ["max_examples", "2.5", "Example filenames must be between 0 and 20."],
  ] as const)("rejects %s = %s", (field, value, message) => {
    const result = configurationFormSchema.safeParse({ ...valid(), [field]: value });

    expect(result.error?.issues[0]?.message).toBe(message);
  });

  it("rejects a temperature out of range", () => {
    const values = valid();
    values.filing.temperature = "2.5";

    expect(configurationFormSchema.safeParse(values).success).toBe(false);
  });

  it("rejects a blank name", () => {
    expect(configurationFormSchema.safeParse({ ...valid(), name: "  " }).success).toBe(false);
  });
});

describe("describing a configuration", () => {
  it("names the reading and filing as the list shows them", () => {
    const vision = configuration({
      reading_method: "vision",
      reading: { ...configuration().reading, endpoint_name: "Tower", model_name: "qwen2.5vl" },
      filing_strategy: "two_calls",
    });

    expect(describeReading(configuration())).toBe("Text layer");
    expect(describeReading(vision)).toBe("Tower · qwen2.5vl");
    expect(describeFiling(vision)).toBe("Tower · qwen3:8b · two calls");
  });
});
