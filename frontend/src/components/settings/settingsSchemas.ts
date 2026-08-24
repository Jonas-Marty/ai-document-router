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

// SPEC 7.3's exact https/private-network-http rule lives in services/settings.py
// (_validate_ai_endpoint_url, with real RFC1918/localhost matching) -- reproducing that
// precisely here would drift the moment one side changes. This is feedback only: catch the
// obviously-wrong case (no scheme at all) client-side, and let the save's own error message
// (which is authoritative) explain a rejected private-network http:// URL.
export const aiFormSchema = z.object({
  ai_endpoint_url: z
    .string()
    .min(1, "AI endpoint URL is required.")
    .refine((value) => /^https?:\/\//.test(value.trim()), "Must start with http:// or https://."),
  ai_model_name: z.string().min(1, "Model name is required."),
  ai_api_key: z.string(), // empty = leave unchanged
});

export type AiFormValues = z.infer<typeof aiFormSchema>;
