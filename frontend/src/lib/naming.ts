// Mirrors backend/app/services/naming.py exactly (SPEC 7.1/7.2). This is feedback only --
// the backend copy is what actually protects anything (CLAUDE.md rule 6).

export const MAX_STEM_LENGTH = 200;
export const MAX_FOLDER_NAME_LENGTH = 100;

// SPEC 7.1. Backslash is included because some intermediaries fold it into a separator.
const FORBIDDEN_CHARS = new Set(["/", "\\", ":", "*", "?", '"', "<", ">", "|"]);

// SPEC 7.1: no leading or trailing dot, space, or hyphen.
const EDGE_CHARS = new Set([".", " ", "-"]);

// Unicode category Cc is C0 controls (code points 0-31) plus C1 controls (127-159), same
// set Python's unicodedata.category(char) == "Cc" matches in naming.py. Checked by code
// point rather than a regex literal to avoid embedding raw control bytes in this file.
function isControlChar(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  return (code >= 0 && code <= 31) || (code >= 127 && code <= 159);
}

function trimEdgeChars(value: string): string {
  let start = 0;
  let end = value.length;
  while (start < end && EDGE_CHARS.has(value.charAt(start))) start++;
  while (end > start && EDGE_CHARS.has(value.charAt(end - 1))) end--;
  return value.slice(start, end);
}

/** Validates a filename stem or folder name against SPEC 7.1/7.2's character rules.
 * Returns an error message, or null if valid. `label` matches the backend's wording so a
 * mismatch between frontend and backend messages never happens. */
export function validateNameCharacters(
  value: string,
  maxLength: number,
  label: string,
): string | null {
  const name = value.trim();
  if (!name) return `${label} is required.`;
  if (name.length > maxLength) return `${label} must be ${maxLength} characters or fewer.`;

  const found = [...name].filter((ch) => FORBIDDEN_CHARS.has(ch));
  if (found.length > 0) {
    const listed = [...new Set(found)].sort().join(" ");
    return `${label} can't contain ${listed}`;
  }
  if ([...name].some(isControlChar)) return `${label} can't contain control characters.`;
  if (name.includes("..")) return `${label} can't contain '..'`;
  if (trimEdgeChars(name) !== name) {
    return `${label} can't start or end with a dot, space, or hyphen.`;
  }
  return null;
}

export function validateStem(value: string): string | null {
  return validateNameCharacters(value, MAX_STEM_LENGTH, "File name");
}

export function validateFolderName(value: string): string | null {
  const error = validateNameCharacters(value, MAX_FOLDER_NAME_LENGTH, "Folder name");
  if (error) return error;
  if (value.trim().includes("/")) return "Folder name can't contain a slash.";
  return null;
}

/** SPEC 8.10's "trimmed silently on blur" behaviour: strips plain whitespace and the SPEC
 * 7.1 edge characters (dot, space, hyphen) without surfacing an error for it. */
export function trimStemOnBlur(value: string): string {
  return trimEdgeChars(value.trim());
}

/** UX-only check that a path sits inside one of the allowed roots (SPEC 7.2). Not a security
 * boundary -- services/paths.py's assert_within_allowed_roots is; this only avoids sending an
 * approve request the backend will certainly reject. */
export function isWithinAllowedRoot(path: string, allowedRoots: string[]): boolean {
  const normalized = path.replace(/\/+$/, "") || "/";
  return allowedRoots.some((root) => {
    const normalizedRoot = root.replace(/\/+$/, "") || "/";
    return normalized === normalizedRoot || normalized.startsWith(`${normalizedRoot}/`);
  });
}
