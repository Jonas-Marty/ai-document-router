"""The audit log: who did what, when, and what the AI spent doing it (SPEC 6.7).

`record` adds an event to the session and does not commit. The caller commits it together
with the change it describes, so the log cannot claim a change that rolled back, and a change
cannot land without its entry. The one exception is a failed sign-in, which has no change of
its own to ride along with -- that commits here, right before the error is raised.

Nothing secret goes in: settings carry no credentials, an endpoint's key is recorded only as
"changed", and a failed sign-in records the address that was tried and never the password.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, func, tuple_
from sqlmodel import Session, col, select

from app.models import AuditAction, AuditEvent, Document, User
from app.services.ai import UsageSummary
from app.services.times import to_storage, utc_now


@dataclass(frozen=True)
class Actor:
    """Who an event is attributed to: a signed-in person, or the app itself."""

    user_id: str | None
    label: str

    @classmethod
    def system(cls) -> "Actor":
        return cls(user_id=None, label="system")

    @classmethod
    def from_user(cls, user: User) -> "Actor":
        return cls(user_id=user.id, label=user.email)


# What the Activity page's filter offers. Explicit lists rather than prefixes: "ai" is about
# what the models did, which lives under document.*, while endpoint and chain edits are
# configuration and read naturally next to the rest of Settings.
CATEGORIES: dict[str, tuple[AuditAction, ...]] = {
    "documents": (
        AuditAction.document_ingested,
        AuditAction.document_ocr_completed,
        AuditAction.document_ocr_failed,
        AuditAction.document_regenerate_requested,
        AuditAction.documents_retry_failed,
        AuditAction.document_approved,
        AuditAction.document_skipped,
        AuditAction.document_trashed,
        AuditAction.history_reverted,
    ),
    "ai": (AuditAction.document_analyzed, AuditAction.document_compared),
    "settings": (
        AuditAction.settings_updated,
        AuditAction.ai_endpoint_created,
        AuditAction.ai_endpoint_updated,
        AuditAction.ai_endpoint_deleted,
        AuditAction.configuration_created,
        AuditAction.configuration_updated,
        AuditAction.configuration_deleted,
        AuditAction.configuration_defaulted,
    ),
    "auth": (
        AuditAction.auth_registered,
        AuditAction.auth_login,
        AuditAction.auth_login_failed,
        AuditAction.auth_logout,
    ),
}


def record(
    session: Session,
    action: AuditAction,
    *,
    actor: Actor,
    summary: str,
    document_id: str | None = None,
    details: dict[str, object] | None = None,
    usage: Iterable[UsageSummary | None] = (),
) -> AuditEvent:
    """Add an event to the session. The caller's commit is what makes it real.

    `usage` takes several summaries because a comparison runs one analysis per method; the
    token columns are their sum, and `details` keeps whatever breakdown the caller put there.
    """
    summaries = [summary for summary in usage if summary is not None]
    prompt, completion = _token_sums(summaries)
    event = AuditEvent(
        occurred_at=to_storage(utc_now()),
        user_id=_owner(session, document_id) or actor.user_id,
        actor_user_id=actor.user_id,
        actor_label=actor.label,
        action=str(action),
        document_id=document_id,
        summary=summary,
        details=details or {},
        prompt_tokens=prompt if summaries else None,
        completion_tokens=completion if summaries else None,
        total_tokens=prompt + completion if summaries else None,
    )
    session.add(event)
    return event


def _owner(session: Session, document_id: str | None) -> str | None:
    """Whose activity an event about a document is: the document's owner, whoever acted.

    Looks in `session.new` too, because ingest records its event before the document it just
    created has been flushed.
    """
    if document_id is None:
        return None
    for pending in session.new:
        if isinstance(pending, Document) and pending.id == document_id:
            return pending.user_id
    document = session.get(Document, document_id)
    return document.user_id if document is not None else None


def _token_sums(summaries: list[UsageSummary]) -> tuple[int, int]:
    stages = [stage for s in summaries for stage in (s.extraction, s.filing) if stage is not None]
    return (
        sum(stage.prompt_tokens for stage in stages),
        sum(stage.completion_tokens for stage in stages),
    )


def list_events(
    session: Session,
    user_id: str,
    *,
    limit: int,
    after: tuple[datetime, str] | None = None,
    category: str | None = None,
) -> tuple[list[AuditEvent], bool]:
    """Newest first. Returns one page and whether another follows.

    Paged on `(occurred_at, id)`, the same way History is: two events in the same instant are
    ordinary here (an approve and the analysis it followed), and a timestamp-only cursor
    would skip or repeat them at a page boundary.
    """
    statement = (
        select(AuditEvent)
        .where(AuditEvent.user_id == user_id)
        .order_by(col(AuditEvent.occurred_at).desc(), col(AuditEvent.id).desc())
    )
    if category is not None:
        statement = statement.where(col(AuditEvent.action).in_(CATEGORIES[category]))
    if after is not None:
        statement = statement.where(
            tuple_(AuditEvent.occurred_at, AuditEvent.id) < after  # type: ignore[arg-type]
        )

    # One extra row says whether another page exists without a second count query.
    rows = list(session.exec(statement.limit(limit + 1)).all())
    return rows[:limit], len(rows) > limit


@dataclass
class ModelUsage:
    task: str
    model_label: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class UsageTotals:
    days: int
    analyses: int
    comparisons: int
    prompt_tokens: int
    completion_tokens: int
    by_model: list[ModelUsage] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


_SPENDING_ACTIONS = (AuditAction.document_analyzed, AuditAction.document_compared)


def usage_totals(session: Session, user_id: str, days: int) -> UsageTotals:
    """Tokens spent over the last `days`, overall and per task and model.

    The totals are summed in SQL from the typed columns. The per-model breakdown is folded
    here from each event's stored summaries: it is a handful of JSON objects per document,
    and a single person's archive does not produce enough of them in a month to matter.
    """
    since = to_storage(utc_now() - timedelta(days=days))
    in_window = (
        col(AuditEvent.user_id) == user_id,
        col(AuditEvent.occurred_at) >= since,
        col(AuditEvent.action).in_(_SPENDING_ACTIONS),
    )

    prompt, completion = session.exec(
        select(
            func.coalesce(func.sum(AuditEvent.prompt_tokens), 0),
            func.coalesce(func.sum(AuditEvent.completion_tokens), 0),
        ).where(*in_window)
    ).one()

    events = session.exec(select(AuditEvent).where(*in_window)).all()
    by_model: dict[tuple[str, str], ModelUsage] = {}
    for event in events:
        for summary in _stored_summaries(event):
            for task in ("extraction", "filing"):
                stage = summary.get(task)
                if not isinstance(stage, dict):
                    continue
                label = str(stage.get("model_label", ""))
                bucket = by_model.setdefault((task, label), ModelUsage(task, label))
                bucket.prompt_tokens += _int(stage.get("prompt_tokens"))
                bucket.completion_tokens += _int(stage.get("completion_tokens"))
                bucket.calls += _int(stage.get("calls"))

    return UsageTotals(
        days=days,
        analyses=sum(1 for e in events if e.action == AuditAction.document_analyzed),
        comparisons=sum(1 for e in events if e.action == AuditAction.document_compared),
        prompt_tokens=int(prompt or 0),
        completion_tokens=int(completion or 0),
        by_model=sorted(by_model.values(), key=lambda m: (-m.total_tokens, m.task, m.model_label)),
    )


def _stored_summaries(event: AuditEvent) -> list[dict[str, object]]:
    """The UsageSummary JSON objects an event carries: one for an analysis, one per method
    for a comparison."""
    details = event.details or {}
    found: list[object] = [details.get("usage")]
    methods = details.get("methods")
    if isinstance(methods, list):
        found += [method.get("usage") for method in methods if isinstance(method, dict)]
    return [summary for summary in found if isinstance(summary, dict)]


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def prune(session: Session, days: int) -> int:
    """Drop events older than `days`. 0 keeps everything. Returns how many went.

    Rows in this table are the only copy of nothing but themselves; clearing old ones is
    retention, not the WebDAV delete CLAUDE.md rule 1 forbids.
    """
    if days <= 0:
        return 0
    cutoff = to_storage(utc_now() - timedelta(days=days))
    result = session.exec(delete(AuditEvent).where(col(AuditEvent.occurred_at) < cutoff))
    session.commit()
    return int(result.rowcount or 0)


def usage_details(usage: UsageSummary | None) -> dict[str, object]:
    """The `details` fragment an analysis stores its cost under, when it had one."""
    return {"usage": usage.to_json()} if usage else {}
