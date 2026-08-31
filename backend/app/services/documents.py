"""Reading documents and the review queue."""

from sqlalchemy import case, func
from sqlmodel import Session, select

from app.models import Document, DocumentStatus, Proposal, ProposalStatus
from app.schemas import AIProposalRead, DocumentRead
from app.services.errors import NotFoundError

# SPEC 5: pending first by scanned_at, then skipped by skip_count then scanned_at. Written
# as an explicit CASE rather than ordering on the status column -- that happens to work today
# only because "pending" sorts before "skipped" alphabetically, which is luck, not intent.
_STATUS_RANK = case(
    (Document.status == DocumentStatus.pending, 0),  # type: ignore[arg-type]
    else_=1,
)

QUEUED_STATUSES = (DocumentStatus.pending, DocumentStatus.skipped)


def get_document(session: Session, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None:
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
    )


def to_read_schema(session: Session, document: Document) -> DocumentRead:
    return DocumentRead.from_models(document, read_proposal(session, document.id))


def retry_failed_proposals(session: Session) -> int:
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
        .where(Document.proposal_status == ProposalStatus.failed)
        # A document that has already been filed keeps its proposal_status; retrying those
        # would burn LLM calls on documents nobody is going to review again.
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
    ).all()

    for document in failed:
        document.proposal_status = ProposalStatus.pending
        document.proposal_error = None
        session.add(document)
    session.commit()
    return len(failed)


def queue(session: Session, limit: int = 20) -> tuple[list[DocumentRead], int]:
    """The review queue in SPEC 5 order, plus how many documents are waiting overall."""
    documents = session.exec(
        select(Document)
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
        .order_by(_STATUS_RANK, Document.skip_count, Document.scanned_at)  # type: ignore[arg-type]
        .limit(limit)
    ).all()

    total = session.exec(
        select(func.count()).select_from(Document).where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
    ).one()

    return [to_read_schema(session, document) for document in documents], int(total)
