from typing import Annotated

from fastapi import APIRouter, Cookie
from sqlalchemy import func
from sqlmodel import select

from app.deps import SessionDep
from app.models import Document
from app.schemas import HealthResponse
from app.services import auth as auth_service
from app.services import settings as settings_service
from app.services import webdav
from app.services.documents import QUEUED_STATUSES
from app.services.errors import AuthenticationRequired

router = APIRouter()


# AUTH: deliberately left unauthenticated -- the deploy healthcheck calls this without
# credentials. See DECISIONS.md.
@router.get("/health")
def get_health(
    session: SessionDep,
    session_cookie: Annotated[str | None, Cookie(alias=auth_service.SESSION_COOKIE_NAME)] = None,
) -> HealthResponse:
    """Liveness, plus -- for a signed-in browser -- the two things its UI needs to know.

    Always 200, even when WebDAV is down: `webdav_reachable` is the signal, and returning
    503 here would take the container unhealthy over a dependency outage. Without a session
    it says only that the API is up: the Nextcloud and the queue are someone's in particular,
    and an anonymous caller is no one's.
    """
    try:
        user = auth_service.resolve_session(session, session_cookie)
    except AuthenticationRequired:
        return HealthResponse(
            status="ok", webdav_configured=False, webdav_reachable=None, queue_depth=0
        )

    app_settings = settings_service.get_settings(session, user.id)
    connection = settings_service.connection_for(app_settings)
    # Every open document: the Inbox's To analyse and Ready groups together. QUEUED_STATUSES
    # is imported rather than restated so the two cannot disagree about what is waiting.
    queue_depth = session.exec(
        select(func.count())
        .select_from(Document)
        .where(Document.user_id == user.id)
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
    ).one()

    return HealthResponse(
        status="ok",
        webdav_configured=connection is not None,
        webdav_reachable=webdav.probe_reachable(connection) if connection else None,
        queue_depth=int(queue_depth),
    )
