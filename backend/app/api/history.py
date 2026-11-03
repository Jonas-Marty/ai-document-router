from fastapi import APIRouter, Query
from sqlalchemy import tuple_
from sqlmodel import select

from app.api import cursors
from app.deps import CurrentUserDep, SessionDep, WebDavDep
from app.models import Document, HistoryEntry
from app.schemas import HistoryEntryRead, HistoryPage, RevertResponse
from app.services import documents as documents_service
from app.services import router as router_service
from app.services.audit import Actor

router = APIRouter()


@router.get("/history")
def read_history(
    session: SessionDep,
    user: CurrentUserDep,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
) -> HistoryPage:
    """Newest first, cursor-paginated.

    The cursor carries `(processed_at, id)` rather than the timestamp alone: two documents
    approved in the same second are ordinary, and a timestamp-only cursor silently skips or
    repeats rows at the page boundary.
    """
    statement = (
        select(HistoryEntry)
        .join(Document, Document.id == HistoryEntry.document_id)  # type: ignore[arg-type]
        .where(Document.user_id == user.id)
    ).order_by(
        HistoryEntry.processed_at.desc(),  # type: ignore[attr-defined]
        HistoryEntry.id.desc(),  # type: ignore[attr-defined]
    )

    if cursor:
        processed_at, entry_id = cursors.decode(cursor)
        statement = statement.where(
            tuple_(HistoryEntry.processed_at, HistoryEntry.id)  # type: ignore[arg-type]
            < (processed_at, entry_id)
        )

    # One extra row tells us whether another page exists without a second count query.
    rows = session.exec(statement.limit(limit + 1)).all()
    page = rows[:limit]
    next_cursor = (
        cursors.encode(page[-1].processed_at, page[-1].id) if len(rows) > limit and page else None
    )

    return HistoryPage(
        items=[HistoryEntryRead.from_model(entry) for entry in page],
        next_cursor=next_cursor,
    )


@router.post("/history/{history_id}/revert")
def revert_entry(
    history_id: str, session: SessionDep, user: CurrentUserDep, webdav: WebDavDep
) -> RevertResponse:
    entry, document = router_service.revert(
        session, webdav, user.id, history_id, actor=Actor.from_user(user)
    )
    return RevertResponse(
        history_entry=HistoryEntryRead.from_model(entry),
        document=documents_service.to_read_schema(session, document),
    )
