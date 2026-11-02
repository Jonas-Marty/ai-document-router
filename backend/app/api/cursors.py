"""Opaque `(timestamp, id)` page cursors, shared by History and the audit log.

The id is part of the cursor rather than the timestamp alone: two rows written in the same
instant are ordinary in both, and a timestamp-only cursor silently skips or repeats rows at
the page boundary.
"""

from datetime import datetime

from app.services.errors import ValidationError


def encode(timestamp: datetime, row_id: str) -> str:
    return f"{timestamp.isoformat()}|{row_id}"


def decode(cursor: str) -> tuple[datetime, str]:
    raw_timestamp, separator, row_id = cursor.partition("|")
    if not separator or not row_id:
        raise ValidationError("That pagination cursor isn't valid.")
    try:
        # Parsed rather than passed through as text: the column is a DateTime, and comparing
        # it against a string only happens to work on SQLite.
        timestamp = datetime.fromisoformat(raw_timestamp)
    except ValueError as exc:
        raise ValidationError("That pagination cursor isn't valid.") from exc
    return timestamp, row_id
