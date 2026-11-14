"""The Inbox list, thumbnails and the bulk actions on it (SPEC 6.4b)."""

from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.deps import (
    AppSettingsDep,
    CurrentUserDep,
    OptionalWebDavDep,
    SessionDep,
    WebDavDep,
)
from app.jobs import poller
from app.schemas import (
    AnalysedResponse,
    AnalyseRequest,
    AnalysisEstimateRead,
    BulkTrashResponse,
    DocumentIdsRequest,
    EstimateRequest,
    InboxCardRead,
    InboxCounts,
    InboxPageRead,
    TrashFailureRead,
)
from app.services import inbox
from app.services.audit import Actor

router = APIRouter()


@router.get("/inbox")
def read_inbox(
    session: SessionDep,
    user: CurrentUserDep,
    webdav: OptionalWebDavDep,
    group: inbox.Group = inbox.Group.ready,
    q: str = Query("", max_length=200),
    sender: str | None = Query(None, max_length=200),
    folder: str | None = Query(None, max_length=1000),
    sort: inbox.Sort = inbox.Sort.newest,
    offset: int = Query(0, ge=0),
    limit: int = Query(48, ge=1, le=inbox.MAX_PAGE),
) -> InboxPageRead:
    result = inbox.page(
        session,
        webdav,
        user.id,
        group=group,
        query=q,
        sender=sender or None,
        folder=folder or None,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return InboxPageRead(
        items=[InboxCardRead.model_validate(card, from_attributes=True) for card in result.items],
        total=result.total,
        counts=InboxCounts.model_validate(result.counts),
        senders=result.senders,
        folders=result.folders,
        analysing=result.analysing,
    )


@router.get("/documents/{document_id}/thumbnail")
def read_thumbnail(
    document_id: str, session: SessionDep, user: CurrentUserDep, webdav: OptionalWebDavDep
) -> Response:
    data = inbox.thumbnail(session, webdav, user.id, document_id)
    # Private: it is a picture of someone's mail. A day is safe because the cache key on
    # the server is the content, and a card's thumbnail never needs to be fresher than that.
    return Response(
        data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"}
    )


@router.post("/documents/analyse")
def analyse_documents(
    payload: AnalyseRequest, session: SessionDep, user: CurrentUserDep
) -> AnalysedResponse:
    queued = inbox.analyse(
        session,
        user.id,
        payload.document_ids,
        actor=Actor.from_user(user),
        configuration_id=payload.configuration_id,
    )
    if queued:
        poller.kick()
    return AnalysedResponse(queued=queued)


@router.post("/documents/trash")
def trash_documents(
    payload: DocumentIdsRequest,
    session: SessionDep,
    user: CurrentUserDep,
    webdav: WebDavDep,
    app_settings: AppSettingsDep,
) -> BulkTrashResponse:
    trashed, failures = inbox.trash(
        session, webdav, app_settings, payload.document_ids, actor=Actor.from_user(user)
    )
    return BulkTrashResponse(
        trashed=trashed,
        failed=[TrashFailureRead(id=failure.id, message=failure.message) for failure in failures],
    )


@router.post("/documents/estimate")
def estimate_analysis(
    payload: EstimateRequest, session: SessionDep, user: CurrentUserDep
) -> AnalysisEstimateRead:
    result = inbox.estimate(session, user.id, payload.document_ids, payload.configuration_id)
    return AnalysisEstimateRead.model_validate(result, from_attributes=True)
