"""One timezone convention, enforced at the two boundaries that would otherwise break it.

WebDAV hands us tz-aware UTC (verified: webdav4 parses `getlastmodified` into
`datetime(..., tzinfo=timezone.utc)`), but SQLite silently drops `tzinfo` on write, so an
aware value read back is naive. Serialising that naive value without a marker would make the
frontend read a UTC instant as local time -- hours wrong, and invisible in tests that only
compare instants.

So: aware UTC everywhere in memory, naive UTC in the database, explicit 'Z' on the way out.
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current time, tz-aware. Never use datetime.now() bare -- it is naive and local."""
    return datetime.now(UTC)


def to_utc_aware(value: datetime) -> datetime:
    """Coerce any datetime to aware UTC. A naive value is assumed to already be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_storage(value: datetime) -> datetime:
    """Aware UTC -> naive UTC, for a SQLite DateTime column.

    Done explicitly so the tzinfo loss is a decision rather than a silent side effect.
    """
    return to_utc_aware(value).replace(tzinfo=None)


def from_storage(value: datetime) -> datetime:
    """Naive UTC out of the database -> aware UTC, so it serialises with an offset."""
    return to_utc_aware(value)
