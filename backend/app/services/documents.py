"""Reading documents and the review queue."""

from sqlalchemy import case, func
from sqlmodel import Session, select

from app.models import Document, DocumentStatus, Proposal
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
