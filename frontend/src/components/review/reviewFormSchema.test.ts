import { zodResolver } from "@hookform/resolvers/zod";
import { describe, expect, it } from "vitest";
import type { AIProposal } from "@/services/api/types";
import {
  approveRequestFor,
  effectiveName,
  formValuesFor,
  type ReviewFormValues,
  reviewFormSchema,
} from "./reviewFormSchema";

// Exercises the schema through the exact resolver react-hook-form calls, not just
// schema.safeParse -- the two have disagreed before across zod major versions.
const resolver = zodResolver(reviewFormSchema);
const options = { shouldUseNativeValidation: false, fields: {} };

function values(overrides: Partial<ReviewFormValues> = {}): ReviewFormValues {
  return {
    documentDate: "2026-05-13",
    sender: "WIR Bank",
    account: "738.953-48.5800",
    title: "Kontoeröffnungsbestätigung",
    period: "",
    number: "",
    handWritten: false,
    name: "",
    folderPath: "/Documents/Bank",
    ...overrides,
  };
}

const proposal: AIProposal = {
  suggested_name: "2026.05.13 WIR Bank Konto",
  fields: { sender: "WIR Bank", account: null, title: "Konto", period: null, number: null },
  target_folder_path: "/Documents/Bank",
  document_date: "2026-05-13",
  confidence_score: 0.9,
  reasoning_text: "",
  model_name: "m",
  connection_name: null,
  configuration_name: null,
  reading_label: null,
  prompt_text: null,
  system_prompt: "",
};

describe("reviewFormSchema via zodResolver", () => {
  it("accepts valid fields and assembles the name in template order", async () => {
    expect((await resolver(values(), undefined, options)).errors).toEqual({});
    expect(effectiveName(values())).toBe(
      "2026.05.13 WIR Bank 738.953-48.5800 Kontoeröffnungsbestätigung",
    );
  });

  it("requires the date, sender and title, but not the optional fields", async () => {
    const { errors } = await resolver(
      values({ documentDate: "", sender: " ", title: "", account: "" }),
      undefined,
      options,
    );
    expect(errors.documentDate?.message).toBe("Document date is required.");
    expect(errors.sender?.message).toBe("Sender is required.");
    expect(errors.title?.message).toBe("Title is required.");
    expect(errors.account).toBeUndefined();
  });

  it("names the field holding a forbidden character", async () => {
    const { errors } = await resolver(values({ period: "01/2026" }), undefined, options);
    expect(errors.period?.message).toBe("Period can't contain /");
  });

  it("checks a hand-written name by SPEC 7.1 and ignores the fields", async () => {
    const { errors } = await resolver(
      values({ handWritten: true, name: "bad/name", sender: "", documentDate: "" }),
      undefined,
      options,
    );
    expect(errors.name?.message).toMatch(/can't contain/);
    expect(errors.sender).toBeUndefined();
    expect(errors.documentDate).toBeUndefined();
  });

  it("requires a non-empty folder path", async () => {
    const { errors } = await resolver(values({ folderPath: "" }), undefined, options);
    expect(errors.folderPath?.message).toMatch(/choose a target folder/i);
  });
});

describe("formValuesFor", () => {
  it("opens a field proposal on its fields", () => {
    const form = formValuesFor(proposal);
    expect(form.handWritten).toBe(false);
    expect([form.sender, form.title, form.account]).toEqual(["WIR Bank", "Konto", ""]);
  });

  it("opens a proposal from before fields existed on its whole name, hand-written", () => {
    const form = formValuesFor({ ...proposal, fields: null, suggested_name: "Old free name" });
    expect(form.handWritten).toBe(true);
    expect(form.name).toBe("Old free name");
  });

  it("opens on empty fields when there is no proposal", () => {
    expect(formValuesFor(null).handWritten).toBe(false);
  });
});

describe("approveRequestFor", () => {
  it("sends the trimmed fields, with blank optional ones as null", () => {
    expect(approveRequestFor(values({ sender: " WIR Bank " }))).toEqual({
      final_folder_path: "/Documents/Bank",
      document_date: "2026-05-13",
      name_fields: {
        sender: "WIR Bank",
        account: "738.953-48.5800",
        title: "Kontoeröffnungsbestätigung",
        period: null,
        number: null,
      },
    });
  });

  it("sends a hand-written name instead of fields", () => {
    const body = approveRequestFor(values({ handWritten: true, name: "My name" }));
    expect(body).toEqual({
      final_folder_path: "/Documents/Bank",
      document_date: "2026-05-13",
      final_name: "My name",
    });
  });
});
