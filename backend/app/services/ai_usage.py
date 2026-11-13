"""What the AI connections cost, per call, for the AI usage page.

Each analysis and comparison stores the calls its meter collected, in the same transaction
as the audit event that describes it. The page asks for a period and gets the calls summed
per day, connection, model and feature; everything else -- grouping, chart buckets, shares
-- is arithmetic on those rows and happens in the browser.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import uuid4

from sqlalchemy import ColumnElement, func
from sqlalchemy import select as sa_select
from sqlmodel import Session, col, select

from app.models import AiCall, AiConfiguration, AiEndpoint, ReadingMethod
from app.services import ai
from app.services.errors import ValidationError
from app.services.times import to_storage, utc_now

COMPARISON = "comparison"
MAX_DAYS = 3 * 366


def store(
    session: Session,
    meters: list[ai.UsageMeter],
    *,
    user_id: str,
    document_id: str | None,
    comparison: bool = False,
) -> None:
    """Add one row per call to the session; the caller's commit makes them real."""
    run_id = str(uuid4())
    occurred_at = to_storage(utc_now())
    for meter in meters:
        for call in meter.calls:
            usage = call.usage
            session.add(
                AiCall(
                    user_id=user_id,
                    occurred_at=occurred_at,
                    run_id=run_id,
                    document_id=document_id,
                    feature=COMPARISON if comparison else call.task,
                    endpoint_id=call.endpoint_id,
                    connection=call.connection or call.label,
                    model=call.model or call.label,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                    cached_tokens=usage.cached_tokens if usage else 0,
                    without_usage=int(usage is None and not call.failed),
                    failed=int(call.failed),
                    fallback=int(call.fallback),
                    duration_ms=call.duration_ms,
                    timed=1,
                    cost=call.cost,
                )
            )


@dataclass
class UsageRow:
    day: str
    endpoint_id: str | None
    connection: str
    connection_exists: bool
    model: str
    model_configured: bool
    feature: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    without_usage: int
    failed: int
    fallback: int
    duration_ms: int
    timed: int
    # None when no call in the row had a price, which is not the same as costing nothing.
    cost: float | None = None


@dataclass
class UsageReport:
    rows: list[UsageRow]
    analyses: int
    comparisons: int


def report(session: Session, user_id: str, start: date, end: date) -> UsageReport:
    """Calls from `start` to `end`, both inclusive, summed per day, connection, model and
    feature. Days are UTC days, like every other stored time."""
    if end < start:
        raise ValidationError("The period ends before it starts.")
    if (end - start).days > MAX_DAYS:
        raise ValidationError("Choose a period of three years or less.")
    window = (
        col(AiCall.user_id) == user_id,
        col(AiCall.occurred_at) >= datetime.combine(start, time.min),
        col(AiCall.occurred_at) < datetime.combine(end + timedelta(days=1), time.min),
    )
    day = func.date(col(AiCall.occurred_at))
    keys = [
        col(AiCall.endpoint_id),
        col(AiCall.connection),
        col(AiCall.model),
        col(AiCall.feature),
    ]
    sums = [
        func.sum(col(column))
        for column in (
            AiCall.requests,
            AiCall.prompt_tokens,
            AiCall.completion_tokens,
            AiCall.cached_tokens,
            AiCall.without_usage,
            AiCall.failed,
            AiCall.fallback,
            AiCall.duration_ms,
            AiCall.timed,
            AiCall.cost,
        )
    ]
    # Core select from a list: sqlmodel's typed overloads stop at a handful of columns.
    grouped = session.execute(
        sa_select(day, *keys, *sums).where(*window).group_by(day, *keys).order_by(day)
    ).all()

    endpoints = set(session.exec(select(AiEndpoint.id).where(AiEndpoint.user_id == user_id)))
    configured: set[tuple[str | None, str | None]] = set()
    for row in session.exec(
        select(AiConfiguration).where(AiConfiguration.user_id == user_id)
    ).all():
        configured.add((row.filing_endpoint_id, row.filing_model_name))
        if row.reading_method == ReadingMethod.vision:
            configured.add((row.reading_endpoint_id, row.reading_model_name))
    rows = [
        UsageRow(
            day=str(r[0]),
            endpoint_id=r[1],
            connection=r[2],
            connection_exists=r[1] in endpoints,
            model=r[3],
            model_configured=(r[1], r[3]) in configured,
            feature=r[4],
            requests=int(r[5]),
            prompt_tokens=int(r[6]),
            completion_tokens=int(r[7]),
            cached_tokens=int(r[8]),
            without_usage=int(r[9]),
            failed=int(r[10]),
            fallback=int(r[11]),
            duration_ms=int(r[12]),
            timed=int(r[13]),
            cost=None if r[14] is None else float(r[14]),
        )
        for r in grouped
    ]

    # Distinct runs, because an analysis spans both the extraction and filing features.
    def runs(*where: ColumnElement[bool]) -> int:
        return int(
            session.exec(
                select(func.count(func.distinct(col(AiCall.run_id)))).where(*window, *where)
            ).one()
        )

    analyses = runs(col(AiCall.feature) != COMPARISON)
    comparisons = runs(col(AiCall.feature) == COMPARISON)
    return UsageReport(rows=rows, analyses=analyses, comparisons=comparisons)
