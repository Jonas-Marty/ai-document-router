import { z } from "zod";
import { normalizeFolderPath } from "@/lib/naming";

function isAbsolutePath(value: string): boolean {
  return value.trim().startsWith("/");
}

// SPEC 7.3, mirrored here for feedback only -- services/settings.py's
// _validate_allowed_roots/_validate_trash_folder is the real boundary (CLAUDE.md rule 6).
export const foldersFormSchema = z
  .object({
    allowed_root_folders: z
      .array(z.object({ value: z.string() }))
      .min(1, "At least one allowed root folder is required."),
    trash_folder_path: z.string(),
    watch_folder_path: z.string(),
  })
  .superRefine((values, ctx) => {
    const roots = values.allowed_root_folders.map((r) => r.value.trim());

    roots.forEach((root, i) => {
      if (!root) {
        ctx.addIssue({
          code: "custom",
          message: "Folder path is required.",
          path: ["allowed_root_folders", i, "value"],
        });
      } else if (!isAbsolutePath(root)) {
        ctx.addIssue({
          code: "custom",
          message: "Must be an absolute path, starting with /.",
          path: ["allowed_root_folders", i, "value"],
        });
      }
    });

    const normalized = roots.map(normalizeFolderPath);
    normalized.forEach((root, i) => {
      if (normalized.indexOf(root) !== i) {
        ctx.addIssue({
          code: "custom",
          message: "Duplicate folder.",
          path: ["allowed_root_folders", i, "value"],
        });
      }
    });

    for (let i = 0; i < normalized.length; i++) {
      for (let j = 0; j < normalized.length; j++) {
        if (i === j) continue;
        const a = normalized[i];
        const b = normalized[j];
        if (a && b && a !== b && (b === a || b.startsWith(`${a}/`))) {
          ctx.addIssue({
            code: "custom",
            message: `Must not be a prefix of '${b}'.`,
            path: ["allowed_root_folders", i, "value"],
          });
        }
      }
    }

    const trash = values.trash_folder_path.trim();
    if (!trash) {
      ctx.addIssue({
        code: "custom",
        message: "Trash folder is required.",
        path: ["trash_folder_path"],
      });
    } else if (!isAbsolutePath(trash)) {
      ctx.addIssue({
        code: "custom",
        message: "Must be an absolute path, starting with /.",
        path: ["trash_folder_path"],
      });
    } else {
      const normalizedTrash = normalizeFolderPath(trash);
      const insideARoot = normalized.some(
        (root) => root && (normalizedTrash === root || normalizedTrash.startsWith(`${root}/`)),
      );
      if (insideARoot) {
        ctx.addIssue({
          code: "custom",
          message: "Trash folder must not be inside any allowed root folder.",
          path: ["trash_folder_path"],
        });
      }
    }

    // May sit inside a root (a common layout), but not be or contain one: filed documents
    // landing directly in it would be ingested again.
    const watch = values.watch_folder_path.trim();
    if (!watch) {
      ctx.addIssue({
        code: "custom",
        message: "Watch folder is required.",
        path: ["watch_folder_path"],
      });
    } else if (!isAbsolutePath(watch)) {
      ctx.addIssue({
        code: "custom",
        message: "Must be an absolute path, starting with /.",
        path: ["watch_folder_path"],
      });
    } else {
      const normalizedWatch = normalizeFolderPath(watch);
      const within = (outer: string, inner: string) =>
        outer === "/" || inner === outer || inner.startsWith(`${outer}/`);
      if (normalized.some((root) => root && within(normalizedWatch, root))) {
        ctx.addIssue({
          code: "custom",
          message: "Watch folder must not be an allowed root folder or contain one.",
          path: ["watch_folder_path"],
        });
      } else if (trash && isAbsolutePath(trash)) {
        const normalizedTrash = normalizeFolderPath(trash);
        if (within(normalizedTrash, normalizedWatch) || within(normalizedWatch, normalizedTrash)) {
          ctx.addIssue({
            code: "custom",
            message: "Watch folder and trash folder must not overlap.",
            path: ["watch_folder_path"],
          });
        }
      }
    }
  });

export type FoldersFormValues = z.infer<typeof foldersFormSchema>;

// SPEC 7.1: "If filename_pattern is set and does not match: warning only." That requires the
// pattern to actually compile -- SPEC 7.3's "live regex validity check" is what stops the
// user from saving a pattern that can never match anything (or that the backend would 422 on
// save, per services/settings.py's own re.compile check).
export const namingFormSchema = z.object({
  filename_pattern: z.string().refine((value) => {
    if (!value.trim()) return true;
    try {
      new RegExp(value);
      return true;
    } catch {
      return false;
    }
  }, "Not a valid regular expression."),
  filename_pattern_hint: z.string(),
});

export type NamingFormValues = z.infer<typeof namingFormSchema>;

export const analysisFormSchema = z.object({
  // Mirrors MIN/MAX_BATCH_SIZE in services/settings.py.
  proposal_batch_size: z.string().refine((value) => {
    const size = Number(value);
    return Number.isInteger(size) && size >= 1 && size <= 50;
  }, "Between 1 and 50 documents."),
});

export type AnalysisFormValues = z.infer<typeof analysisFormSchema>;

// SPEC 7.3's exact https/private-network-http rule lives in services/connections.py
// (validate_endpoint_url, with real RFC1918/localhost matching) -- reproducing that precisely
// here would drift the moment one side changes. This is feedback only: catch the
// obviously-wrong case (no scheme at all) client-side, and let the save's own error message
// (which is authoritative) explain a rejected public http:// URL.
const optionalPrice = z.string().refine((value) => {
  if (value.trim() === "") return true;
  const price = Number(value);
  return Number.isFinite(price) && price >= 0 && price <= 10_000;
}, "Between 0 and 10,000, or blank.");

export const endpointFormSchema = z.object({
  name: z.string().refine((value) => value.trim().length > 0, "Give the endpoint a name."),
  base_url: z
    .string()
    .refine((value) => /^https?:\/\//.test(value.trim()), "Must start with http:// or https://."),
  api_key: z.string(), // empty = leave the stored key unchanged
  timeout_seconds: z.string().refine((value) => {
    const seconds = Number(value);
    return Number.isInteger(seconds) && seconds >= 10 && seconds <= 900;
  }, "Between 10 and 900 seconds."),
  // Blank = unknown, so the backend sends without checking the prompt fits (SPEC 6.3b).
  context_limit: z.string().refine((value) => {
    if (value.trim() === "") return true;
    const tokens = Number(value);
    return Number.isInteger(tokens) && tokens >= 1024 && tokens <= 2_000_000;
  }, "Between 1,024 and 2,000,000 tokens, or blank."),
  json_mode: z.enum(["auto", "json_schema", "json_object", "none"]),
  price_input_per_million: optionalPrice,
  price_output_per_million: optionalPrice,
});

/** Blank form text as null, anything else as the number it spells. */
export function optionalNumber(value: string): number | null {
  return value.trim() === "" ? null : Number(value);
}

export type EndpointFormValues = z.infer<typeof endpointFormSchema>;

export const nextcloudFormSchema = z.object({
  base_url: z
    .string()
    .refine((value) => /^https?:\/\//.test(value.trim()), "Must start with https:// or http://."),
  username: z.string().refine((value) => value.trim().length > 0, "Enter your username."),
  password: z.string(), // empty = keep the stored app password
});

export type NextcloudFormValues = z.infer<typeof nextcloudFormSchema>;
