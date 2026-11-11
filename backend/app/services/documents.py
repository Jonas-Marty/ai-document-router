"""Reading documents and the review queue."""

from sqlalchemy import case, func
from sqlmodel import Session, select

from app.models import AuditAction, Document, DocumentStatus, Proposal, ProposalStatus
from app.schemas import AIProposalRead, DocumentRead
from app.services import ai_tasks, audit, progress
from app.services.audit import Actor
from app.services.errors import NotFoundError

# SPEC 5: pending first by scanned_at, then skipped by skip_count then scanned_at. Written
# as an explicit CASE rather than ordering on the status column -- that happens to work today
# only because "pending" sorts before "skipped" alphabetically, which is luck, not intent.
_STATUS_RANK = case(
    (Document.status == DocumentStatus.pending, 0),  # type: ignore[arg-type]
    else_=1,
)

QUEUED_STATUSES = (DocumentStatus.pending, DocumentStatus.skipped)


def get_document(session: Session, user_id: str, document_id: str) -> Document:
    """Someone else's document is as missing as a deleted one: 404, never 403, so an id
    cannot be used to find out that a document exists (SPEC 6.5)."""
    document = session.get(Document, document_id)
    if document is None or document.user_id != user_id:
        raise NotFoundError("That document no longer exists.")
    return document


def read_proposal(session: Session, document_id: str) -> AIProposalRead | None:
    proposal = session.exec(select(Proposal).where(Proposal.document_id == document_id)).first()
    if proposal is None:
        return None
    return AIProposalRead(
        suggested_name=proposal.suggested_name,
        target_folder_path=proposal.target_folder_path,
        document_date=proposal.document_date,
        confidence_score=proposal.confidence_score,
        reasoning_text=proposal.reasoning_text,
        model_name=proposal.model_name,
        connection_name=proposal.endpoint_name,
        chosen_by_user=proposal.chosen_by_user,
        fallback_from=proposal.fallback_from,
        prompt_text=proposal.prompt_text,
    )


def to_read_schema(session: Session, document: Document) -> DocumentRead:
    return DocumentRead.from_models(
        document, read_proposal(session, document.id), progress.stage_of(document.id)
    )


def request_regenerate(
    session: Session,
    user_id: str,
    document_id: str,
    *,
    actor: Actor,
    endpoint_id: str | None = None,
    model_name: str | None = None,
) -> Document:
    """Queue a fresh proposal. The poller does the work; this only resets the status.

    With `endpoint_id` and `model_name` the filing step runs on exactly that pair instead of
    the configured chain. The endpoint is checked against the user here, so the poller never
    sees an id that is not theirs.
    """
    document = get_document(session, user_id, document_id)
    label = ""
    if endpoint_id is not None and model_name is not None:
        endpoint = ai_tasks.get_endpoint(session, user_id, endpoint_id)
        label = f" with {endpoint.name} · {model_name.strip()}"
        document.rerun_endpoint_id = endpoint.id
        document.rerun_model_name = model_name.strip()
    else:
        document.rerun_endpoint_id = None
        document.rerun_model_name = None
    document.proposal_status = ProposalStatus.pending
    document.proposal_error = None
    session.add(document)
    audit.record(
        session,
        AuditAction.document_regenerate_requested,
        actor=actor,
        document_id=document.id,
        summary=f"Asked for a new proposal for {document.original_filename}{label}",
    )
    session.commit()
    session.refresh(document)
    return document


def retry_failed_proposals(session: Session, user_id: str, *, actor: Actor) -> int:
    """Put every failed proposal in the queue back to pending. Returns how many moved.

    The poller only ever looks at `pending` documents, so a proposal that failed stays failed
    for good -- which is right for a per-document problem, but wrong for the common case,
    where the *configuration* was the problem and one fix in Settings makes every one of them
    retryable at once. Without this the only route back is opening all of them one at a time
    and pressing Try again on each.

    Documents that failed for a reason no setting can change (no text layer) are included
    rather than filtered out: telling those apart means parsing `proposal_error` prose, and
    they re-fail on extraction without costing an LLM call.
    """
    failed = session.exec(
        select(Document)
        .where(Document.user_id == user_id)
        .where(Document.proposal_status == ProposalStatus.failed)
        # A document that has already been filed keeps its proposal_status; retrying those
        # would burn LLM calls on documents nobody is going to review again.
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
    ).all()

    for document in failed:
        document.proposal_status = ProposalStatus.pending
        document.proposal_error = None
        document.rerun_endpoint_id = None
        document.rerun_model_name = None
        session.add(document)
    audit.record(
        session,
        AuditAction.documents_retry_failed,
        actor=actor,
        summary=f"Retried {len(failed)} failed proposal{'' if len(failed) == 1 else 's'}",
        details={"count": len(failed), "document_ids": [document.id for document in failed]},
    )
    session.commit()
    return len(failed)


def queue(session: Session, user_id: str, limit: int = 20) -> tuple[list[DocumentRead], int]:
    """One user's review queue in SPEC 5 order, plus how many of their documents wait."""
    documents = session.exec(
        select(Document)
        .where(Document.user_id == user_id)
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
        .order_by(_STATUS_RANK, Document.skip_count, Document.scanned_at)  # type: ignore[arg-type]
        .limit(limit)
    ).all()

    total = session.exec(
        select(func.count())
        .select_from(Document)
        .where(Document.user_id == user_id)
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
    ).one()

    return [to_read_schema(session, document) for document in documents], int(total)
