from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import select

from app.deps import SessionDep
from app.models import Document
from app.schemas import HealthResponse
from app.services import webdav
from app.services.documents import QUEUED_STATUSES

router = APIRouter()


# AUTH: deliberately left unauthenticated -- the deploy healthcheck calls this without
# credentials. See DECISIONS.md.
@router.get("/health")
def get_health(session: SessionDep) -> HealthResponse:
    """Liveness plus the two things the UI needs to know.

    Always 200, even when WebDAV is down: `webdav_reachable` is the signal, and returning
    503 here would take the container unhealthy over a dependency outage.
    """
    # Same definition as /queue's total_pending, imported rather than restated: if the
    # two diverged, the outage banner and the queue screen would disagree about how
    # much work is waiting.
    queue_depth = session.exec(
        select(func.count()).select_from(Document).where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
    ).one()

    return HealthResponse(
        status="ok",
        webdav_reachable=webdav.probe_reachable(),
        queue_depth=int(queue_depth),
    )
