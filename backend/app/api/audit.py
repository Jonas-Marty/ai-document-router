"""The audit log, for the admin only (SPEC 6.7).

Admin-only because it records every account's sign-ins and every change anyone made; on a
shared instance that is not something every account should read.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api import cursors
from app.deps import AdminUserDep, SessionDep
from app.schemas import AuditEventRead, AuditPage, AuditUsageRead, ModelUsageRead
from app.services import audit

router = APIRouter()

Category = Literal["documents", "ai", "settings", "auth"]


@router.get("/audit")
def read_audit(
    session: SessionDep,
    _admin: AdminUserDep,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
    category: Annotated[Category | None, Query()] = None,
) -> AuditPage:
    """Newest first, cursor-paginated like History."""
    events, more = audit.list_events(
        session,
        limit=limit,
        after=cursors.decode(cursor) if cursor else None,
        category=category,
    )
    last = events[-1] if events else None
    return AuditPage(
        items=[AuditEventRead.from_model(event) for event in events],
        next_cursor=cursors.encode(last.occurred_at, last.id) if more and last else None,
    )


@router.get("/audit/usage")
def read_usage(
    session: SessionDep, _admin: AdminUserDep, days: int = Query(30, ge=1, le=366)
) -> AuditUsageRead:
    totals = audit.usage_totals(session, days)
    return AuditUsageRead(
        days=totals.days,
        analyses=totals.analyses,
        comparisons=totals.comparisons,
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
        total_tokens=totals.total_tokens,
        by_model=[
            ModelUsageRead(
                task=model.task,
                model_label=model.model_label,
                prompt_tokens=model.prompt_tokens,
                completion_tokens=model.completion_tokens,
                total_tokens=model.total_tokens,
                calls=model.calls,
            )
            for model in totals.by_model
        ],
    )
