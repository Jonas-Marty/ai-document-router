"""Analysing documents: each configuration's reading and filing steps (SPEC 6.3a).

Two rules shape everything here. Nothing tries another connection, model or reading method
on its own: a configuration is one answer to "how was this read and filed". And each step
runs as soon as the connection it needs is reachable, so a document read on a text layer is
not held up by a GPU that is switched off, and one waiting for that GPU says so instead of
failing.

Every failure that is not a connection being out of reach lands on `proposal_status=failed`
with a readable message rather than an exception: the document stays approvable by hand.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import case
from sqlmodel import Session, select

from app.models import (
    AiTask,
    AppSettings,
    AuditAction,
    Document,
    FilingStrategy,
    OcrStatus,
    Proposal,
    ProposalStatus,
    Reading,
    ReadingMethod,
)
from app.services import (
    ai,
    ai_usage,
    audit,
    configurations,
    connections,
    extraction,
    folders,
    metrics,
    ocr,
    progress,
    searchable,
    transcribe,
)
from app.services.ai import Unreachable
from app.services.configurations import Plan, ReadingSetup
from app.services.connections import ResolvedStep
from app.services.documents import QUEUED_STATUSES
from app.services.errors import AppError
from app.services.extraction import ExtractedDocument
from app.services.times import to_storage, utc_now
from app.services.webdav import WebDavService

logger = logging.getLogger(__name__)

SYSTEM = audit.Actor.system()
WAITING_FOR_CONFIGURATION = "Waiting for a configuration — create one in the Lab."
NO_ROOTS = "No allowed folders are configured yet — set them in Settings."
# Between the two messages of the two-call strategy, in what is stored and shown.
SECOND_CALL = "--- Second call: the folder ---"

Prober = Callable[[ResolvedStep], bool]


@dataclass
class _Job:
    document: Document
    plan: Plan
    meter: ai.UsageMeter = field(default_factory=ai.UsageMeter)
    reading: Reading | None = None


class _Reachability:
    """Which connections answered this tick. Each is probed at most once per batch."""

    def __init__(self, probe: Prober) -> None:
        self._probe = probe
        self._known: dict[str, bool] = {}

    def check(self, step: ResolvedStep) -> bool:
        key = step.endpoint_id or step.endpoint_url
        if key not in self._known:
            self._known[key] = self._probe(step)
        return self._known[key]

    def lost(self, step: ResolvedStep) -> None:
        self._known[step.endpoint_id or step.endpoint_url] = False


class _ReadingFailed(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def run_batch(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    fresh: dict[str, ExtractedDocument] | None = None,
    *,
    probe: Prober | None = None,
) -> tuple[int, int]:
    """One tick's analyses for one user. Returns (ready, failed); waiting counts as neither.

    `fresh` is what ingest already extracted this tick, so a new document's text layer is
    not downloaded a second time. `probe` defaults to a real request to each connection,
    looked up per call so tests can replace it.
    """
    documents = session.exec(
        select(Document)
        .where(Document.user_id == app_settings.user_id)
        .where(Document.proposal_status == ProposalStatus.pending)
        # Filed documents keep their proposal_status; analysing one would attach a proposal
        # to a document nobody is going to review.
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
        # Waiting ones last, so documents that can run are not crowded out of the batch by
        # ones that will probably wait again.
        .order_by(
            case((Document.waiting_reason.is_(None), 0), else_=1),  # type: ignore[union-attr]
            Document.discovered_at,  # type: ignore[arg-type]
        )
        .limit(app_settings.proposal_batch_size)
    ).all()
    if not documents:
        return 0, 0
    if not app_settings.allowed_root_folders:
        for document in documents:
            _fail(session, document, NO_ROOTS)
        return 0, len(documents)

    jobs: list[_Job] = []
    for document in documents:
        configuration = configurations.for_document(session, document)
        if configuration is None:
            _wait(session, document, WAITING_FOR_CONFIGURATION)
            continue
        jobs.append(_Job(document, configurations.plan(session, configuration)))

    reachability = _Reachability(probe or connections.is_reachable)
    unread = _read_all(session, service, jobs, fresh or {}, reachability)
    ready, failed = _file_all(session, service, app_settings, jobs, reachability)
    return ready, failed + unread


def _group(step: ResolvedStep | None) -> tuple[str, str]:
    return (step.endpoint_id or step.endpoint_url, step.model_name) if step else ("", "")


def _read_all(
    session: Session,
    service: WebDavService,
    jobs: Sequence[_Job],
    fresh: dict[str, ExtractedDocument],
    reachability: _Reachability,
) -> int:
    """Every reading the batch still needs, before any filing. Returns how many failed.

    Readings that need no model go first; model readings are grouped by connection and model,
    so on a GPU that holds one model at a time each is loaded once per tick.
    """
    failed = 0
    to_read: list[tuple[_Job, ReadingSetup]] = []
    for job in jobs:
        setup = job.plan.reading_setup(searchable=job.document.ocr_status is OcrStatus.ready)
        job.reading = session.exec(
            select(Reading).where(
                Reading.document_id == job.document.id, Reading.setup_key == setup.key
            )
        ).first()
        if job.reading is None:
            to_read.append((job, setup))
    to_read.sort(
        key=lambda item: (
            item[1].method == ReadingMethod.vision,
            _group(item[0].plan.reading_step),
        )
    )

    for job, setup in to_read:
        document = job.document
        step = job.plan.reading_step
        with progress.tracking(document.id):
            if setup.method == ReadingMethod.vision:
                if step is None:
                    _fail(
                        session,
                        document,
                        f"The configuration '{job.plan.configuration_name}' has no reading "
                        "connection — choose one in the Lab.",
                    )
                    failed += 1
                    continue
                if not reachability.check(step):
                    _wait(session, document, f"Waiting for {step.endpoint_name} to read")
                    continue
            progress.report("Reading the document…")
            try:
                text = _read(service, job, setup, fresh.get(document.id))
            except Unreachable as exc:
                assert step is not None
                reachability.lost(step)
                _wait(
                    session,
                    document,
                    f"Waiting for {step.endpoint_name} to read ({exc.message})",
                    job.meter,
                )
                continue
            except _ReadingFailed as exc:
                _fail(session, document, exc.reason, job.meter)
                failed += 1
                continue
            job.reading = _store_reading(session, document, setup, text)
        # tracking() clears the stage on the way out; say why it is still waiting.
        progress.set_stage(document.id, "Read; waiting for the other documents to be read…")
    return failed


def _read(
    service: WebDavService,
    job: _Job,
    setup: ReadingSetup,
    fresh: ExtractedDocument | None,
) -> str:
    document = job.document
    try:
        if setup.method == ReadingMethod.text_layer:
            extracted = fresh if fresh is not None else _extract(service, document)
            if extracted.text_error is not None:
                # The OCR failure's own reason when there is one: "This PDF is encrypted, so
                # it can't be OCR'd" is something the person can act on.
                raise _ReadingFailed(document.ocr_error or extracted.text_error)
            return extracted.text
        data = _document_bytes(service, document)
    except AppError as exc:
        raise _ReadingFailed(f"Couldn't read the file: {exc.message}") from exc

    if setup.method == ReadingMethod.tesseract:
        return _tesseract(data, document)

    step = job.plan.reading_step
    assert step is not None
    try:
        transcription = transcribe.transcribe(
            data,
            step,
            system_prompt=job.plan.reading_system_prompt,
            instructions=job.plan.reading_instructions,
            parameters=job.plan.reading_parameters,
            meter=job.meter,
        )
    except Unreachable:
        raise
    except AppError as exc:
        raise _ReadingFailed(f"Reading with {step.label} failed: {exc.message}") from exc
    except extraction.RenderUnavailable as exc:
        raise _ReadingFailed(str(exc)) from exc
    if not transcription.markdown:
        raise _ReadingFailed(f"Reading with {step.label} returned no text.")
    return transcription.markdown


def _tesseract(data: bytes, document: Document) -> str:
    if document.mime_type != extraction.PDF_MIME:
        raise _ReadingFailed("Only PDFs can be rendered to pages for Tesseract.")
    try:
        text = ocr.read_pages(extraction.render_pages(data))
    except (ocr.OcrUnavailable, extraction.RenderUnavailable) as exc:
        raise _ReadingFailed(str(exc)) from exc
    if not text.strip():
        raise _ReadingFailed("Tesseract read no text off these pages.")
    return text


def _extract(service: WebDavService, document: Document) -> ExtractedDocument:
    data = _document_bytes(service, document)
    return extraction.extract(data, document.original_filename, document.mime_type)


def _document_bytes(service: WebDavService, document: Document) -> bytes:
    """The searchable copy when one is waiting, otherwise the file on the server.

    Not an optimisation: until the document is filed, the cache is the only place the OCR
    text exists. Reading the original here instead would have the app OCR a scan, keep the
    result, and still tell the person no text layer could be found.
    """
    if document.ocr_status is OcrStatus.ready:
        cached = searchable.load(document.content_hash)
        if cached is not None:
            return cached
    return b"".join(service.read_stream(document.webdav_path))


def _store_reading(session: Session, document: Document, setup: ReadingSetup, text: str) -> Reading:
    reading = Reading(
        user_id=document.user_id,
        document_id=document.id,
        setup_key=setup.key,
        method=setup.method,
        label=setup.label,
        text=text,
        created_at=to_storage(utc_now()),
    )
    session.add(reading)
    session.commit()
    session.refresh(reading)
    return reading


def _file_all(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    jobs: Sequence[_Job],
    reachability: _Reachability,
) -> tuple[int, int]:
    readable = [
        job
        for job in jobs
        if job.reading is not None and job.document.proposal_status == ProposalStatus.pending
    ]
    readable.sort(key=lambda job: _group(job.plan.filing_step))
    context: tuple[list[str], list[str]] | None = None
    ready = failed = 0
    for job in readable:
        document, reading, step = job.document, job.reading, job.plan.filing_step
        assert reading is not None
        with progress.tracking(document.id):
            if step is None:
                _fail(
                    session,
                    document,
                    f"The configuration '{job.plan.configuration_name}' has no filing "
                    "connection — choose one in the Lab.",
                    job.meter,
                    reading,
                )
                failed += 1
                continue
            if not reachability.check(step):
                _wait(
                    session, document, f"Read · waiting for {step.endpoint_name} to file", job.meter
                )
                continue
            progress.report("Starting the analysis…")
            if context is None:
                # Once per batch: it walks the folder tree, and every document shares it.
                context = folders.prompt_context(service, app_settings)
            try:
                filed = file_text(job.plan, reading.text, context, app_settings, job.meter)
            except Unreachable as exc:
                reachability.lost(step)
                _wait(
                    session,
                    document,
                    f"Read · waiting for {step.endpoint_name} to file ({exc.message})",
                    job.meter,
                )
                continue
            except ai.ProposalRejected as exc:
                _fail(session, document, exc.reason, job.meter, reading)
                failed += 1
                continue
            except AppError as exc:
                _fail(session, document, exc.message, job.meter, reading)
                failed += 1
                continue
            _store(session, document, filed, job.plan, reading, job.meter)
            ready += 1
    return ready, failed


@dataclass(frozen=True)
class Filed:
    """A validated proposal and exactly what was sent for it."""

    proposal: ai.Proposal
    prompt_text: str
    system_prompt: str


def file_text(
    plan: Plan,
    text: str,
    context: tuple[list[str], list[str]],
    app_settings: AppSettings,
    meter: ai.UsageMeter,
) -> Filed:
    """Run the configuration's filing step over a reading (SPEC 6.3).

    Raises what the calls raise: Unreachable, other AIUnavailable, ProposalRejected.
    """
    step = plan.filing_step
    if step is None:
        raise ai.AIUnavailable(
            f"The configuration '{plan.configuration_name}' has no filing connection."
        )
    text = text[: plan.max_text_chars]
    tree, samples = context
    roots = list(app_settings.allowed_root_folders)
    folder_choices = ai.prompt_folders(tree, plan.max_folders)
    hint = app_settings.filename_pattern_hint

    def common(called: ResolvedStep) -> dict[str, object]:
        return {
            "endpoint_url": called.endpoint_url,
            "model_name": called.model_name,
            "api_key": called.api_key,
            "usage": called.usage(meter, AiTask.filing),
            "timeout": called.timeout_seconds,
            "parameters": plan.filing_parameters,
            "context_limit": called.context_limit,
            "json_mode": called.json_mode,
        }

    if plan.filing_strategy == FilingStrategy.one_call:
        prompt = ai.build_prompt(
            text,
            tree,
            samples,
            hint,
            plan.filing_instructions,
            max_folders=plan.max_folders,
            max_examples=plan.max_examples,
        )
        proposal = connections.call_step(
            step,
            lambda called: ai.request_proposal(
                **common(called),  # type: ignore[arg-type]
                prompt=prompt,
                allowed_roots=roots,
                folder_choices=folder_choices,
                system_prompt=plan.filing_system_prompt,
            ),
            AiTask.filing,
        )
        return Filed(proposal, prompt, plan.filing_system_prompt)

    fields_prompt = ai.build_prompt(
        text,
        tree,
        samples,
        hint,
        plan.filing_instructions,
        with_folders=False,
        max_folders=plan.max_folders,
        max_examples=plan.max_examples,
    )
    fields = connections.call_step(
        step,
        lambda called: ai.request_fields(
            **common(called),  # type: ignore[arg-type]
            prompt=fields_prompt,
            system_prompt=plan.filing_system_prompt,
        ),
        AiTask.filing,
    )
    folder_prompt = ai.build_folder_prompt(
        text,
        tree,
        fields.document_date,
        fields.fields,
        plan.filing_instructions,
        max_folders=plan.max_folders,
    )
    proposal = connections.call_step(
        step,
        lambda called: ai.request_folder(
            **common(called),  # type: ignore[arg-type]
            prompt=folder_prompt,
            allowed_roots=roots,
            fields=fields,
            folder_choices=folder_choices,
            system_prompt=plan.folder_system_prompt,
        ),
        AiTask.filing,
    )
    return Filed(
        proposal,
        f"{fields_prompt}\n\n{SECOND_CALL}\n\n{folder_prompt}",
        f"{plan.filing_system_prompt}\n\n{SECOND_CALL}\n\n{plan.folder_system_prompt}",
    )


def _store(
    session: Session,
    document: Document,
    filed: Filed,
    plan: Plan,
    reading: Reading,
    meter: ai.UsageMeter,
) -> None:
    """Replace any existing proposal wholesale (SPEC 4.1) inside one transaction."""
    proposal = filed.proposal
    existing = session.exec(select(Proposal).where(Proposal.document_id == document.id)).first()
    if existing is not None:
        session.delete(existing)
        # Flush so the DELETE reaches the database before the INSERT below; otherwise the
        # unique constraint on proposal.document_id can trip within a single flush.
        session.flush()

    session.add(
        Proposal(
            document_id=document.id,
            suggested_name=proposal.suggested_name,
            target_folder_path=proposal.target_folder_path,
            document_date=proposal.document_date,
            **(proposal.fields.to_json() if proposal.fields else {}),
            confidence_score=proposal.confidence_score,
            reasoning_text=proposal.reasoning_text,
            model_name=proposal.model_name,
            created_at=to_storage(utc_now()),
            prompt_text=filed.prompt_text,
            system_prompt=filed.system_prompt,
            endpoint_name=plan.filing_step.endpoint_name if plan.filing_step else None,
            configuration_id=plan.configuration_id,
            configuration_name=plan.configuration_name,
            reading_label=reading.label,
        )
    )
    usage = meter.summary()
    document.proposal_status = ProposalStatus.ready
    document.proposal_error = None
    document.waiting_reason = None
    document.reading_id = reading.id
    document.analysis_usage = usage.to_json() if usage else None
    session.add(document)
    audit.record(
        session,
        AuditAction.document_analyzed,
        actor=SYSTEM,
        document_id=document.id,
        summary=(
            f"Proposed {proposal.suggested_name} in {proposal.target_folder_path} "
            f"for {document.original_filename}"
        ),
        details={
            "outcome": "ready",
            "suggested_name": proposal.suggested_name,
            "target_folder_path": proposal.target_folder_path,
            "confidence_score": proposal.confidence_score,
            "configuration": plan.configuration_name,
            "reading": reading.label,
            "filing_model": proposal.model_name,
            "duration_ms": meter.elapsed_ms(),
            **audit.usage_details(usage),
        },
        usage=[usage],
    )
    ai_usage.store(session, [meter], user_id=document.user_id, document_id=document.id)
    session.commit()
    metrics.analyses.labels("ready").inc()
    logger.info(
        "Proposal ready for %s: %s%s",
        document.webdav_path,
        proposal.suggested_name,
        _usage_suffix(usage),
    )


def _fail(
    session: Session,
    document: Document,
    reason: str,
    meter: ai.UsageMeter | None = None,
    reading: Reading | None = None,
) -> None:
    """Also replaces the stored usage: it describes the latest analysis, and this was it --
    including the tokens a rejected reply cost, and None when no model was reached at all."""
    usage = meter.summary() if meter else None
    document.proposal_status = ProposalStatus.failed
    document.proposal_error = reason
    document.waiting_reason = None
    if reading is not None:
        document.reading_id = reading.id
    document.analysis_usage = usage.to_json() if usage else None
    session.add(document)
    audit.record(
        session,
        AuditAction.document_analyzed,
        actor=SYSTEM,
        document_id=document.id,
        summary=f"Couldn't propose a name for {document.original_filename}: {reason}",
        details={
            "outcome": "failed",
            "error": reason,
            "reading": reading.label if reading else None,
            "duration_ms": meter.elapsed_ms() if meter else 0,
            **audit.usage_details(usage),
        },
        usage=[usage],
    )
    if meter is not None:
        ai_usage.store(session, [meter], user_id=document.user_id, document_id=document.id)
    session.commit()
    metrics.analyses.labels("failed").inc()
    logger.info("Proposal failed for %s: %s%s", document.webdav_path, reason, _usage_suffix(usage))


def _wait(
    session: Session, document: Document, reason: str, meter: ai.UsageMeter | None = None
) -> None:
    """Leave the document pending with the reason; the next tick tries again.

    Not audited: a computer that is off for a weekend would otherwise write an event per
    document per minute. Tokens already spent on it (a reading before the filing connection
    went away) are stored, since they were paid for.
    """
    if meter is not None and meter.calls:
        usage = meter.summary()
        document.analysis_usage = usage.to_json() if usage else None
        ai_usage.store(session, [meter], user_id=document.user_id, document_id=document.id)
    if document.waiting_reason != reason:
        logger.info("%s is waiting: %s", document.webdav_path, reason)
    document.waiting_reason = reason
    session.add(document)
    session.commit()


def _usage_suffix(usage: ai.UsageSummary | None) -> str:
    return f" ({usage.describe()})" if usage else ""
