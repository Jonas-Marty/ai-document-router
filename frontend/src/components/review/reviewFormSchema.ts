import { z } from "zod";
import { validateStem } from "@/lib/naming";

// SPEC 8.3's field order 2-4: document date, file name (stem only -- the extension is a
// fixed chip, never editable per SPEC 7.1), target folder path.
//
// The `useForm()` that uses this schema (ReviewPage) MUST pass `mode: "onChange"`. The
// action bar disables Approve on `formState.isValid` with no separate submit step -- Approve
// *is* the submit -- and react-hook-form's default "onSubmit" mode doesn't populate
// `formState.errors`/`isValid` until a submit has already happened once, which would leave
// Approve enabled on an invalid form until the user tries it and fails. Confirmed against a
// real browser: with the default mode, typing a forbidden character showed no error at all
// until submit.
export const reviewFormSchema = z.object({
  documentDate: z.string(), // "" means null; ReviewForm converts on submit.
  name: z.string().superRefine((value, ctx) => {
    const error = validateStem(value);
    if (error) ctx.addIssue(error);
  }),
  folderPath: z.string().min(1, "Choose a target folder."),
});

export type ReviewFormValues = z.infer<typeof reviewFormSchema>;

// YYYY-MM-DD, matching what the backend accepts (AIProposal.document_date / SPEC 4.2).
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function isValidDateInput(value: string): boolean {
  if (value === "") return true;
  return DATE_RE.test(value);
}
