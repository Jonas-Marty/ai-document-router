"""Revert. The list of what was filed is the Inbox's Done and Trashed tabs (SPEC 6.4b)."""

from fastapi import APIRouter

from app.deps import CurrentUserDep, SessionDep, WebDavDep
from app.schemas import HistoryEntryRead, RevertResponse
from app.services import documents as documents_service
from app.services import router as router_service
from app.services.audit import Actor

router = APIRouter()


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
