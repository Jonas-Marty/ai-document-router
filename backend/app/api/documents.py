from collections.abc import Iterator
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.deps import AppSettingsDep, CurrentUserDep, SessionDep, WebDavDep
from app.models import AppSettings, ProposalStatus
from app.schemas import (
    ApproveRequest,
    DocumentRead,
    DocumentResponse,
    HistoryEntryRead,
    QueueResponse,
    RoutedResponse,
)
from app.services import documents as documents_service
from app.services import router as router_service
from app.services.errors import NotFoundError

router = APIRouter()


@router.get("/queue")
def read_queue(
    session: SessionDep, _user: CurrentUserDep, limit: int = Query(20, ge=1, le=100)
) -> QueueResponse:
    items, total = documents_service.queue(session, limit)
    return QueueResponse(items=items, total_pending=total)


@router.get("/documents/{document_id}")
def read_document(document_id: str, session: SessionDep, _user: CurrentUserDep) -> DocumentRead:
    document = documents_service.get_document(session, document_id)
    return documents_service.to_read_schema(session, document)


@router.get("/documents/{document_id}/content")
def read_document_content(
    document_id: str, session: SessionDep, _user: CurrentUserDep, webdav: WebDavDep
) -> StreamingResponse:
    """Stream the file's bytes.

    The first chunk is resolved before the response starts. read_stream returns a lazy
    generator, so without this a missing file would raise only after the 200 headers were
    already on the wire -- the browser would see a truncated body instead of a 404.
    """
    document = documents_service.get_document(session, document_id)
    chunks = webdav.read_stream(document.webdav_path)

    try:
        first = next(chunks)
    except StopIteration:
        first = b""

    def body() -> Iterator[bytes]:
        if first:
            yield first
        yield from chunks

    filename = quote(document.original_filename)
    return StreamingResponse(
        body(),
        media_type=document.mime_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{filename}"},
    )


@router.post("/documents/{document_id}/regenerate")
def regenerate_proposal(
    document_id: str, session: SessionDep, _user: CurrentUserDep
) -> DocumentResponse:
    """Queue a fresh proposal.

    Asynchronous: the document goes back to `proposal_status=pending` and the poller picks
    it up. SPEC 8.8 already specifies a "Waiting for the AI proposal" skeleton, so the UI is
    built to wait, and this keeps the request off a call that can take 60s with its retry.
    """
    document = documents_service.get_document(session, document_id)
    settings = session.get(AppSettings, 1)
    if settings is None:
        raise NotFoundError("Settings have not been initialised.")

    document.proposal_status = ProposalStatus.pending
    document.proposal_error = None
    session.add(document)
    session.commit()
    session.refresh(document)

    return DocumentResponse(document=documents_service.to_read_schema(session, document))


@router.post("/documents/{document_id}/approve")
def approve_document(
    document_id: str,
    payload: ApproveRequest,
    session: SessionDep,
    _user: CurrentUserDep,
    webdav: WebDavDep,
    app_settings: AppSettingsDep,
) -> RoutedResponse:
    """File the document. Synchronous, because the user is waiting on the result."""
    document, entry = router_service.approve(
        session,
        webdav,
        app_settings,
        document_id,
        final_name=payload.final_name,
        final_folder_path=payload.final_folder_path,
        document_date=payload.document_date,
    )
    return RoutedResponse(
        document=documents_service.to_read_schema(session, document),
        history_entry=HistoryEntryRead.from_model(entry),
    )


@router.post("/documents/{document_id}/skip")
def skip_document(document_id: str, session: SessionDep, _user: CurrentUserDep) -> DocumentResponse:
    document = router_service.skip(session, document_id)
    return DocumentResponse(document=documents_service.to_read_schema(session, document))


@router.post("/documents/{document_id}/trash")
def trash_document(
    document_id: str,
    session: SessionDep,
    _user: CurrentUserDep,
    webdav: WebDavDep,
    app_settings: AppSettingsDep,
) -> RoutedResponse:
    """Move to the trash folder. There is no delete path anywhere in this codebase."""
    document, entry = router_service.trash(session, webdav, app_settings, document_id)
    return RoutedResponse(
        document=documents_service.to_read_schema(session, document),
        history_entry=HistoryEntryRead.from_model(entry),
    )
