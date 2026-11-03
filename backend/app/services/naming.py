"""Filename validation, SPEC 7.1.

The frontend validates the same rules for feedback; this is the copy that actually protects
anything (CLAUDE.md rule 6).
"""

import re
import unicodedata
from datetime import date

from app.services.errors import ValidationError

MAX_STEM_LENGTH = 200
MAX_FOLDER_NAME_LENGTH = 100

# SPEC 7.1. Backslash is included because some intermediaries fold it into a separator.
FORBIDDEN_CHARS = set('/\\:*?"<>|')

# SPEC 7.1: no leading or trailing dot, space, or hyphen.
_EDGE_CHARS = ". -"


def validate_stem(value: str) -> str:
    """Validate a filename without its extension. Returns the trimmed stem.

    Whitespace is trimmed rather than rejected -- SPEC 7.1 trims silently on blur, so a
    value that only differs by surrounding space is the same name as far as the user is
    concerned.
    """
    return _validate(value, MAX_STEM_LENGTH, "File name")


def validate_folder_name(value: str) -> str:
    """Validate a single new folder name (SPEC 7.2): same characters, 1-100 chars."""
    name = _validate(value, MAX_FOLDER_NAME_LENGTH, "Folder name")
    if "/" in name:
        raise ValidationError("Folder name can't contain a slash.")
    return name


def _validate(value: str, max_length: int, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} is required.")

    name = value.strip()
    if not name:
        raise ValidationError(f"{label} is required.")
    if len(name) > max_length:
        raise ValidationError(f"{label} must be {max_length} characters or fewer.")

    found = FORBIDDEN_CHARS & set(name)
    if found:
        listed = " ".join(sorted(found))
        raise ValidationError(f"{label} can't contain {listed}")
    if any(unicodedata.category(char) == "Cc" for char in name):
        raise ValidationError(f"{label} can't contain control characters.")
    if ".." in name:
        raise ValidationError(f"{label} can't contain '..'")
    if name != name.strip(_EDGE_CHARS):
        raise ValidationError(f"{label} can't start or end with a dot, space, or hyphen.")

    return name


# A date-like token at the start of a name, complete or not: "2026", "2026.04", "2026-04-16",
# "16.04.2026". Only ever replaced when the result then matches the user's own pattern.
_LEADING_DATE = re.compile(
    r"^(?:\d{4}(?:[._-]\d{1,2}){0,2}|\d{1,2}[._-]\d{1,2}[._-]\d{4})(?:\s+|$)"
)
_DATE_FORMATS = ("%Y.%m.%d", "%Y-%m-%d", "%Y%m%d", "%d.%m.%Y")


def align_date_prefix(name: str, document_date: date | None, pattern: str | None) -> str:
    """Put the proposal's own document date at the start of its name, if the pattern wants one.

    Small models get the date field right and then write a truncated one into the name
    ("2026 Helvetia ..." with a document_date of 2026-04-16). The user's filename pattern
    decides whether a date prefix is wanted and in which format: the name is only changed
    when it doesn't match the pattern and the dated version does.
    """
    if not pattern or document_date is None:
        return name
    try:
        expected = re.compile(pattern)
    except re.error:
        return name
    if expected.search(name):
        return name
    rest = _LEADING_DATE.sub("", name, count=1)
    for date_format in _DATE_FORMATS:
        candidate = f"{document_date.strftime(date_format)} {rest}".strip()
        if len(candidate) <= MAX_STEM_LENGTH and expected.search(candidate):
            return candidate
    return name
