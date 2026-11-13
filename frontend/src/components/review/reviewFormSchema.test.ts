import { zodResolver } from "@hookform/resolvers/zod";
import { describe, expect, it } from "vitest";
import type { AIProposal, KnownSender } from "@/services/api/types";
import {
  approveRequestFor,
  effectiveName,
  formValuesFor,
  makeReviewFormSchema,
  type ReviewFormValues,
  rememberPrompts,
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
    rememberSender: false,
    rememberAccount: false,
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
      remember_sender: false,
      remember_account: false,
    });
  });

  it("sends a remember flag only with the question it answers", () => {
    const ticked = values({ rememberSender: true, rememberAccount: true });

    expect(approveRequestFor(ticked, { sender: false, account: true })).toMatchObject({
      remember_sender: false,
      remember_account: true,
    });
    expect(approveRequestFor(values(), { sender: true, account: true })).toMatchObject({
      remember_sender: false,
      remember_account: false,
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

describe("the owner's template", () => {
  it("orders the name and leaves out what it omits", () => {
    const name = effectiveName(values({ number: "77" }), ["sender", "title", "date"]);

    expect(name).toBe("WIR Bank Kontoeröffnungsbestätigung 2026.05.13");
  });

  it("validates the name the template makes", async () => {
    const long = "x".repeat(150);
    const withAll = zodResolver(reviewFormSchema);
    const withoutPeriod = zodResolver(makeReviewFormSchema(["date", "sender", "title"]));

    const both = values({ title: long.slice(0, 100), period: long.slice(0, 120) });
    expect(Object.keys((await withAll(both, undefined, options)).errors)).toEqual(["name"]);
    expect((await withoutPeriod(both, undefined, options)).errors).toEqual({});
  });
});

describe("rememberPrompts", () => {
  const known: KnownSender[] = [
    {
      id: "s1",
      short_name: "WIR Bank",
      aliases: [],
      accounts: [{ id: "a1", value: "738.953-48.5800" }],
    },
  ];

  it("asks about a new sender and its account", () => {
    expect(rememberPrompts(values({ sender: "ZKB", account: "CH33" }), known)).toEqual({
      sender: true,
      account: true,
    });
  });

  it("asks nothing about what is known, compared as the backend compares", () => {
    const same = values({ sender: "wir bank", account: "738 953 48 5800" });

    expect(rememberPrompts(same, known)).toEqual({ sender: false, account: false });
  });

  it("asks only about a new account of a known sender", () => {
    expect(rememberPrompts(values({ account: "999.1" }), known)).toEqual({
      sender: false,
      account: true,
    });
  });

  it("asks nothing for a hand-written name, a blank sender, or before the list has loaded", () => {
    expect(rememberPrompts(values({ handWritten: true }), [])).toEqual({
      sender: false,
      account: false,
    });
    expect(rememberPrompts(values({ sender: " " }), [])).toEqual({
      sender: false,
      account: false,
    });
    expect(rememberPrompts(values(), undefined)).toEqual({ sender: false, account: false });
  });
});
