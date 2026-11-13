"""Reading documents and the review queue."""

from sqlalchemy import case, func
from sqlmodel import Session, select

from app.models import (
    AiConfiguration,
    AuditAction,
    Document,
    DocumentStatus,
    Proposal,
    ProposalStatus,
    Reading,
)
from app.schemas import AIProposalRead, DocumentRead, DocumentReadingRead, NameFieldsBody
from app.services import ai, audit, configurations, naming, progress
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


def proposal_fields(proposal: Proposal) -> dict[str, str | None] | None:
    """The proposal's name fields, or None for a free name from before fields existed."""
    if proposal.sender is None or proposal.title is None:
        return None
    return naming.NameFields(
        sender=proposal.sender,
        title=proposal.title,
        account=proposal.account,
        period=proposal.period,
        number=proposal.number,
    ).to_json()


def _fields_body(fields: dict[str, str | None] | None) -> NameFieldsBody | None:
    return NameFieldsBody.model_validate(fields) if fields else None


def read_proposal(session: Session, document_id: str) -> AIProposalRead | None:
    proposal = session.exec(select(Proposal).where(Proposal.document_id == document_id)).first()
    if proposal is None:
        return None
    return AIProposalRead(
        suggested_name=proposal.suggested_name,
        fields=_fields_body(proposal_fields(proposal)),
        target_folder_path=proposal.target_folder_path,
        document_date=proposal.document_date,
        confidence_score=proposal.confidence_score,
        reasoning_text=proposal.reasoning_text,
        model_name=proposal.model_name,
        connection_name=proposal.endpoint_name,
        configuration_name=proposal.configuration_name,
        reading_label=proposal.reading_label,
        prompt_text=proposal.prompt_text,
        system_prompt=proposal.system_prompt or ai.SYSTEM_PROMPT,
    )


def to_read_schema(session: Session, document: Document) -> DocumentRead:
    reading = session.get(Reading, document.reading_id) if document.reading_id else None
    picked = (
        session.get(AiConfiguration, document.configuration_id)
        if document.configuration_id
        else None
    )
    return DocumentRead.from_models(
        document,
        read_proposal(session, document.id),
        progress.stage_of(document.id),
        reading=DocumentReadingRead(label=reading.label, text=reading.text) if reading else None,
        configuration_name=picked.name if picked and picked.user_id == document.user_id else None,
    )


def request_regenerate(
    session: Session,
    user_id: str,
    document_id: str,
    *,
    actor: Actor,
    configuration_id: str | None = None,
) -> Document:
    """Queue a fresh analysis. The poller does the work; this only resets the status.

    With `configuration_id` the document is analysed with that configuration from now on;
    the default's id clears the pick (SPEC 6.3a). Checked against the user here, so the
    poller never sees an id that is not theirs.
    """
    document = get_document(session, user_id, document_id)
    label = ""
    if configuration_id is not None:
        configuration = configurations.get_configuration(session, user_id, configuration_id)
        label = f" with '{configuration.name}'"
        document.configuration_id = None if configuration.is_default else configuration.id
    document.proposal_status = ProposalStatus.pending
    document.proposal_error = None
    document.waiting_reason = None
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
