from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import select

from app.deps import SessionDep
from app.models import Document, DocumentStatus
from app.schemas import HealthResponse
from app.services import webdav

router = APIRouter()

# Everything still awaiting review: pending plus previously skipped, matching the queue
# ordering in SPEC 5.
_QUEUED_STATUSES = (DocumentStatus.pending, DocumentStatus.skipped)


# AUTH: deliberately left unauthenticated -- the deploy healthcheck calls this without
# credentials. See DECISIONS.md.
@router.get("/health")
def get_health(session: SessionDep) -> HealthResponse:
    """Liveness plus the two things the UI needs to know.

    Always 200, even when WebDAV is down: `webdav_reachable` is the signal, and returning
    503 here would take the container unhealthy over a dependency outage.
    """
    queue_depth = session.exec(
        select(func.count()).select_from(Document).where(Document.status.in_(_QUEUED_STATUSES))  # type: ignore[attr-defined]
    ).one()

    return HealthResponse(
        status="ok",
        webdav_reachable=webdav.probe_reachable(),
        queue_depth=int(queue_depth),
    )
