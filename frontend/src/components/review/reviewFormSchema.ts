import { z } from "zod";
import { assembleName, NAME_FIELDS, validateField, validateStem } from "@/lib/naming";
import type { AIProposal, ApproveRequest } from "@/services/api/types";

// SPEC 8.3's field order 2-4: document date, the name's fields (or the name itself when
// hand-written), target folder path. The extension is a fixed chip, never editable (7.1).
//
// The `useForm()` that uses this schema (ReviewPage) MUST pass `mode: "onChange"`. The
// action bar disables Approve on `formState.isValid` with no separate submit step -- Approve
// *is* the submit -- and react-hook-form's default "onSubmit" mode doesn't populate
// `formState.errors`/`isValid` until a submit has already happened once, which would leave
// Approve enabled on an invalid form until the user tries it and fails. Confirmed against a
// real browser: with the default mode, typing a forbidden character showed no error at all
// until submit.
export const reviewFormSchema = z
  .object({
    documentDate: z.string(), // "" means null; ReviewForm converts on submit.
    sender: z.string(),
    account: z.string(),
    title: z.string(),
    period: z.string(),
    number: z.string(),
    // SPEC 7.1a: a hand-written name replaces the fields; `name` is only read then.
    handWritten: z.boolean(),
    name: z.string(),
    folderPath: z.string().min(1, "Choose a target folder."),
  })
  .superRefine((values, ctx) => {
    if (values.handWritten) {
      const error = validateStem(values.name);
      if (error) ctx.addIssue({ code: "custom", path: ["name"], message: error });
      return;
    }
    if (!values.documentDate) {
      ctx.addIssue({
        code: "custom",
        path: ["documentDate"],
        message: "Document date is required.",
      });
    }
    let fieldsValid = true;
    for (const key of NAME_FIELDS) {
      const error = validateField(key, values[key]);
      if (error) {
        fieldsValid = false;
        ctx.addIssue({ code: "custom", path: [key], message: error });
      }
    }
    // Only once every field is fine, so one mistake is not reported twice.
    if (fieldsValid) {
      const error = validateStem(effectiveName(values));
      if (error) ctx.addIssue({ code: "custom", path: ["name"], message: error });
    }
  });

export type ReviewFormValues = z.infer<typeof reviewFormSchema>;

/** The name that will be filed: the hand-written one, or the one the fields assemble. */
export function effectiveName(values: ReviewFormValues): string {
  return values.handWritten ? values.name : assembleName(values.documentDate, values);
}

/** The form as a proposal fills it. A proposal from before name fields existed opens
 * hand-written, with its whole name; no proposal opens on empty fields. */
export function formValuesFor(proposal: AIProposal | null): ReviewFormValues {
  const fields = proposal?.fields ?? null;
  return {
    documentDate: proposal?.document_date ?? "",
    sender: fields?.sender ?? "",
    account: fields?.account ?? "",
    title: fields?.title ?? "",
    period: fields?.period ?? "",
    number: fields?.number ?? "",
    handWritten: proposal !== null && fields === null,
    name: proposal?.suggested_name ?? "",
    folderPath: proposal?.target_folder_path ?? "",
  };
}

/** What approve sends: the fields for the backend to assemble, or the hand-written name. */
export function approveRequestFor(values: ReviewFormValues): ApproveRequest {
  const base = {
    final_folder_path: values.folderPath,
    document_date: values.documentDate || null,
  };
  if (values.handWritten) return { ...base, final_name: values.name };
  const optional = (value: string) => value.trim() || null;
  return {
    ...base,
    name_fields: {
      sender: values.sender.trim(),
      account: optional(values.account),
      title: values.title.trim(),
      period: optional(values.period),
      number: optional(values.number),
    },
  };
}

// YYYY-MM-DD, matching what the backend accepts (AIProposal.document_date / SPEC 4.2).
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function isValidDateInput(value: string): boolean {
  if (value === "") return true;
  return DATE_RE.test(value);
}
