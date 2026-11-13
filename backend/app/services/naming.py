"""Filename validation and assembly from name fields, SPEC 7.1 and 7.1a.

The frontend validates the same rules for feedback; this is the copy that actually protects
anything (CLAUDE.md rule 6).
"""

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.services.errors import ValidationError

MAX_STEM_LENGTH = 200
MAX_FOLDER_NAME_LENGTH = 100

# SPEC 7.1. Backslash is included because some intermediaries fold it into a separator.
FORBIDDEN_CHARS = set('/\\:*?"<>|')

# SPEC 7.1: no leading or trailing dot, space, or hyphen.
EDGE_CHARS = ". -"


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
    if name != name.strip(EDGE_CHARS):
        raise ValidationError(f"{label} can't start or end with a dot, space, or hyphen.")

    return name


# SPEC 7.1a. The name's parts besides the date, with how they are named in error messages.
FIELD_LABELS: dict[str, str] = {
    "sender": "Sender",
    "account": "Account",
    "title": "Title",
    "period": "Period",
    "number": "Number",
}
REQUIRED_FIELDS = frozenset({"sender", "title"})
# SPEC 6.3c: the owner orders these, and may leave the optional ones out.
TEMPLATE_FIELDS = ("date", *FIELD_LABELS)
DEFAULT_TEMPLATE = TEMPLATE_FIELDS
ALWAYS_IN_TEMPLATE = ("date", "sender", "title")
DATE_FORMAT = "%Y.%m.%d"


@dataclass(frozen=True)
class NameFields:
    """A name's parts besides its date (SPEC 7.1a), trimmed; optional ones None, never ""."""

    sender: str
    title: str
    account: str | None = None
    period: str | None = None
    number: str | None = None

    def to_json(self) -> dict[str, str | None]:
        return {key: getattr(self, key) for key in FIELD_LABELS}


def clean_fields(values: Mapping[str, object]) -> NameFields:
    """Trim and check each field, naming the field in the error. Raises ValidationError."""
    cleaned: dict[str, str | None] = {}
    for key, label in FIELD_LABELS.items():
        value = values.get(key)
        if value is not None and not isinstance(value, str):
            raise ValidationError(f"{label} must be text.")
        text = (value or "").strip()
        if not text:
            if key in REQUIRED_FIELDS:
                raise ValidationError(f"{label} is required.")
            cleaned[key] = None
            continue
        found = FORBIDDEN_CHARS & set(text)
        if found:
            raise ValidationError(f"{label} can't contain {' '.join(sorted(found))}")
        if any(unicodedata.category(char) == "Cc" for char in text):
            raise ValidationError(f"{label} can't contain control characters.")
        cleaned[key] = text
    sender, title = cleaned["sender"], cleaned["title"]
    assert sender is not None and title is not None  # required above
    return NameFields(
        sender=sender,
        title=title,
        account=cleaned["account"],
        period=cleaned["period"],
        number=cleaned["number"],
    )


def assemble_name(
    document_date: date | None,
    fields: NameFields,
    template: Sequence[str] = DEFAULT_TEMPLATE,
) -> str:
    """The fields in the template's order, empty parts skipped, checked by 7.1.

    A missing date leaves it out rather than failing: a model may find none, and the
    review form asks for one before the name can be approved. A field the template leaves
    out is left out of the name even when it has a value.
    """
    values = {"date": document_date.strftime(DATE_FORMAT) if document_date else None}
    values |= fields.to_json()
    return validate_stem(" ".join(part for key in template if (part := values.get(key))))
