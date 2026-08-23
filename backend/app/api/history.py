from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import tuple_
from sqlmodel import select

from app.deps import CurrentUserDep, SessionDep, WebDavDep
from app.models import HistoryEntry
from app.schemas import HistoryEntryRead, HistoryPage, RevertResponse
from app.services import documents as documents_service
from app.services import router as router_service
from app.services.errors import ValidationError

router = APIRouter()


def _encode_cursor(entry: HistoryEntry) -> str:
    return f"{entry.processed_at.isoformat()}|{entry.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    raw_timestamp, separator, entry_id = cursor.partition("|")
    if not separator or not entry_id:
        raise ValidationError("That pagination cursor isn't valid.")
    try:
        # Parsed rather than passed through as text: processed_at is a DateTime column,
        # and comparing it against a string only happens to work on SQLite.
        processed_at = datetime.fromisoformat(raw_timestamp)
    except ValueError as exc:
        raise ValidationError("That pagination cursor isn't valid.") from exc
    return processed_at, entry_id


@router.get("/history")
def read_history(
    session: SessionDep,
    _user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
) -> HistoryPage:
    """Newest first, cursor-paginated.

    The cursor carries `(processed_at, id)` rather than the timestamp alone: two documents
    approved in the same second are ordinary, and a timestamp-only cursor silently skips or
    repeats rows at the page boundary.
    """
    statement = select(HistoryEntry).order_by(
        HistoryEntry.processed_at.desc(),  # type: ignore[attr-defined]
        HistoryEntry.id.desc(),  # type: ignore[attr-defined]
    )

    if cursor:
        processed_at, entry_id = _decode_cursor(cursor)
        statement = statement.where(
            tuple_(HistoryEntry.processed_at, HistoryEntry.id)  # type: ignore[arg-type]
            < (processed_at, entry_id)
        )

    # One extra row tells us whether another page exists without a second count query.
    rows = session.exec(statement.limit(limit + 1)).all()
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if len(rows) > limit and page else None

    return HistoryPage(
        items=[HistoryEntryRead.from_model(entry) for entry in page],
        next_cursor=next_cursor,
    )


@router.post("/history/{history_id}/revert")
def revert_entry(
    history_id: str, session: SessionDep, _user: CurrentUserDep, webdav: WebDavDep
) -> RevertResponse:
    entry, document = router_service.revert(session, webdav, history_id)
    return RevertResponse(
        history_entry=HistoryEntryRead.from_model(entry),
        document=documents_service.to_read_schema(session, document),
    )
