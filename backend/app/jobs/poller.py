"""Scan the watch folder, OCR what needs it, and generate proposals.

SPEC 6.2 rule 4 is the important one: the poller only ever reads. It never moves or deletes
anything -- filing happens when the user approves, not here. The OCR phase keeps that rule:
it writes the searchable copy it produces to the data volume, not to WebDAV. Uploading it
is approve's job, after the move that puts the document where it belongs.

Every phase is capped per tick. A first run against a populated watch folder (the target
Nextcloud has 52 scans sitting in it) would otherwise download every file and make one LLM
call per document inside a single job run, while `max_instances=1` blocks the next tick.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app import db, log
from app.config import settings as config
from app.models import (
    AiTask,
    AppSettings,
    AuditAction,
    Document,
    DocumentStatus,
    OcrStatus,
    Proposal,
    ProposalStatus,
)
from app.services import (
    ai,
    ai_tasks,
    ai_usage,
    audit,
    extraction,
    folders,
    metrics,
    progress,
    searchable,
    transcribe,
)
from app.services import settings as settings_service
from app.services.documents import QUEUED_STATUSES
from app.services.errors import AppError
from app.services.extraction import ExtractedDocument
from app.services.times import to_storage, utc_now
from app.services.webdav import WebDavEntry, WebDavService, build_client

logger = logging.getLogger(__name__)

# Everything the poller does, it does on nobody's direct instruction.
SYSTEM = audit.Actor.system()

# SPEC 6.2 rule 3: extensions that mean "still being written".
PARTIAL_SUFFIXES = (".part", ".tmp", ".crdownload", ".filepart")

_scheduler: BackgroundScheduler | None = None
JOB_ID = "poll_watch_folder"


def start_scheduler() -> None:
    """Start the interval job. No-op when disabled."""
    global _scheduler
    if not config.poller_enabled:
        logger.info("Poller disabled; not scheduling.")
        return
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        run_once,
        "interval",
        seconds=config.poll_interval_seconds,
        # A slow LLM call must not let ticks stack up on top of each other.
        max_instances=1,
        coalesce=True,
        id=JOB_ID,
    )
    _scheduler.start()
    logger.info("Poller started, every %ss.", config.poll_interval_seconds)


def kick() -> None:
    """Run the next tick now rather than at the end of the interval.

    For a person who just asked for something and is watching the screen. A tick that is
    already running is left alone: max_instances=1 skips the extra run, and documents it has
    not reached yet are still picked up by it or by the next one.
    """
    if _scheduler is None:
        return
    job = _scheduler.get_job(JOB_ID)
    if job is not None:
        job.modify(next_run_time=datetime.now(UTC))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def run_once() -> None:
    """One full tick, one pass per connected user. Never raises -- a failing tick must not
    kill the scheduler, and one user's unreachable Nextcloud must not stall everyone else's."""
    started = time.monotonic()
    ingested = ocred_count = ready = failed = 0
    with log.bound(f"tick={log.new_id()}"):
        try:
            with Session(db.engine) as session:
                user_ids = connected_users(session)
                if not user_ids:
                    logger.debug("Nobody has connected a Nextcloud yet; skipping tick.")
                    metrics.poller_ticks.labels("skipped").inc()
                    return
                for user_id in user_ids:
                    counts = _run_for_user(session, user_id)
                    ingested += counts[0]
                    ocred_count += counts[1]
                    ready += counts[2]
                    failed += counts[3]
                searchable.prune(config.ocr_cache_max_age_days)
                audit.prune(session, config.audit_retention_days)
        except Exception:
            logger.exception("Poller tick failed.")
            metrics.poller_ticks.labels("failed").inc()
            return

        elapsed = time.monotonic() - started
        metrics.poller_ticks.labels("ok").inc()
        metrics.poller_tick_duration.observe(elapsed)
        metrics.poller_last_tick.set_to_current_time()

        # Quiet when idle: a tick a minute that found nothing would otherwise be most of the
        # INFO log, and bury the ticks that actually did something.
        busy = ingested or ocred_count or ready or failed
        logger.log(
            logging.INFO if busy else logging.DEBUG,
            "Tick done in %.1fs: ingested %d, OCR %d, proposals %d ready / %d failed.",
            elapsed,
            ingested,
            ocred_count,
            ready,
            failed,
        )


def connected_users(session: Session) -> list[str]:
    """Users with a Nextcloud connection to work on. Nobody else has anything to poll."""
    rows = session.exec(
        select(AppSettings.user_id)
        .where(AppSettings.webdav_base_url != "")
        .where(AppSettings.webdav_password_encrypted.is_not(None))  # type: ignore[union-attr]
        .order_by(AppSettings.user_id)
    ).all()
    return list(rows)


def _run_for_user(session: Session, user_id: str) -> tuple[int, int, int, int]:
    """Ingest, OCR and propose for one user. Returns (ingested, ocred, ready, failed)."""
    # Appended to the tick's tag rather than replacing it, so a line still says which tick.
    with log.bound(f"{log.context_var.get()} user={user_id[:8]}".strip()):
        try:
            app_settings = settings_service.get_settings(session, user_id)
            service = build_service(session, app_settings)
            # Carry the bytes we just read straight into the proposal step, so a document
            # ingested this tick is not downloaded a second time to extract the same text.
            fresh = ingest(session, service, app_settings)
            ingested = len(fresh)
            # Before proposals, not after: a document we just OCR'd has text for the
            # first time, and the whole point is that its proposal is built from that
            # text rather than from the "no text layer" it would otherwise be stuck with.
            ocred = ocr_pending(session, service, app_settings)
            for document_id in ocred:
                # Its cached extraction predates the text layer, so it must not be reused.
                fresh.pop(document_id, None)
            ready, failed = generate_proposals(session, service, app_settings, fresh)
        except Exception:
            session.rollback()
            logger.exception("Poller pass failed for one user; carrying on with the rest.")
            return 0, 0, 0, 0
    return ingested, len(ocred), ready, failed


def build_service(session: Session, app_settings: AppSettings) -> WebDavService:
    connection = settings_service.require_connection(app_settings)
    return WebDavService(
        build_client(connection),
        settings_service.permitted_roots(session, app_settings),
        cache_namespace=connection.cache_namespace,
    )


def ingest(
    session: Session, service: WebDavService, app_settings: AppSettings
) -> dict[str, ExtractedDocument]:
    """Record new files in the watch folder.

    Returns what was extracted from each newly ingested document, keyed by document id, so
    the proposal step in the same tick can reuse it instead of re-downloading.
    """
    watch_folder = app_settings.watch_folder_path
    if not watch_folder:
        logger.debug("No watch folder set in Settings yet; nothing to ingest.")
        return {}
    try:
        entries = service.list_dir(watch_folder)
    except AppError as exc:
        logger.warning("Could not list %s: %s", watch_folder, exc.message)
        return {}

    extracted: dict[str, ExtractedDocument] = {}
    for entry in entries:
        if len(extracted) >= config.poller_ingest_batch:
            logger.info("Ingest cap reached; remaining files wait for the next tick.")
            break
        if not _is_ready(entry):
            continue
        if session.exec(
            select(Document).where(
                Document.user_id == app_settings.user_id, Document.webdav_path == entry.path
            )
        ).first():
            continue
        recorded = _record(session, service, app_settings.user_id, entry)
        if recorded is not None:
            document, document_text = recorded
            extracted[document.id] = document_text
    return extracted


def _is_ready(entry: WebDavEntry) -> bool:
    """Whether a file looks finished rather than mid-upload (SPEC 6.2 rule 3)."""
    if entry.is_dir:
        return False
    if entry.name.lower().endswith(PARTIAL_SUFFIXES):
        return False
    if entry.modified is not None:
        # entry.modified is guaranteed tz-aware UTC by the webdav service, so this
        # subtraction is safe; a naive value here would raise TypeError.
        age = (utc_now() - entry.modified).total_seconds()
        if age < config.poller_min_file_age_seconds:
            logger.debug("Skipping %s: only %.1fs old.", entry.path, age)
            return False
    return True


def _record(
    session: Session, service: WebDavService, user_id: str, entry: WebDavEntry
) -> tuple[Document, ExtractedDocument] | None:
    """Download once and derive everything from those bytes."""
    try:
        data = b"".join(service.read_stream(entry.path))
    except AppError as exc:
        logger.warning("Could not read %s: %s", entry.path, exc.message)
        return None

    extracted = extraction.extract(data, entry.name, entry.content_type)

    # Dedupe guard (SPEC 6.2 rule 1): the same bytes already waiting under another name.
    duplicate = session.exec(
        select(Document).where(
            Document.user_id == user_id,
            Document.content_hash == extracted.content_hash,
            Document.status.in_((DocumentStatus.pending, DocumentStatus.skipped)),  # type: ignore[attr-defined]
        )
    ).first()
    if duplicate is not None:
        logger.info("Skipping %s: same content as %s.", entry.path, duplicate.webdav_path)
        return None

    now = utc_now()
    document = Document(
        user_id=user_id,
        webdav_path=entry.path,
        original_filename=entry.name,
        mime_type=extracted.mime_type,
        file_size_bytes=extracted.file_size_bytes,
        page_count=extracted.page_count,
        content_hash=extracted.content_hash,
        scanned_at=to_storage(entry.modified or now),
        discovered_at=to_storage(now),
        status=DocumentStatus.pending,
        proposal_status=ProposalStatus.pending,
    )
    if extracted.text_error is not None:
        # Still fully approvable by hand -- SPEC 6.3.
        document.proposal_status = ProposalStatus.failed
        document.proposal_error = extracted.text_error
        if extracted.mime_type == extraction.PDF_MIME:
            # A PDF whose pages carry no text is exactly what OCR is for. Decided here,
            # where the bytes have just been read, rather than making the OCR phase
            # download every queued document to find out. It re-checks anyway.
            document.ocr_status = OcrStatus.pending

    session.add(document)
    audit.record(
        session,
        AuditAction.document_ingested,
        actor=SYSTEM,
        document_id=document.id,
        summary=f"Found {entry.name} in the watch folder",
        details={
            "path": entry.path,
            "size_bytes": extracted.file_size_bytes,
            "page_count": extracted.page_count,
            "text_error": extracted.text_error,
        },
    )
    session.commit()
    metrics.documents_ingested.inc()
    logger.info("Ingested %s (%d bytes).", entry.path, extracted.file_size_bytes)
    return document, extracted


def ocr_pending(session: Session, service: WebDavService, app_settings: AppSettings) -> list[str]:
    """Produce a searchable copy for queued documents whose pages carry no text.

    Here rather than in approve because ocrmypdf takes tens of seconds to minutes, and
    approve is synchronous with a person waiting on it (SPEC 6.4). The copy waits on the
    data volume until they file the document. Returns the ids that were OCR'd this tick.
    """
    if not app_settings.store_ocr_text:
        return []
    if not searchable.is_available():
        logger.debug("ocrmypdf is not installed; skipping the OCR phase.")
        return []

    pending = session.exec(
        select(Document)
        .where(Document.user_id == app_settings.user_id)
        .where(Document.ocr_status == OcrStatus.pending)
        # Same reason proposals are limited this way: a document already filed is not going
        # to be reviewed again, and OCRing it would spend minutes on nobody's behalf.
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
        .order_by(Document.discovered_at)  # type: ignore[arg-type]
        .limit(config.poller_ocr_batch)
    ).all()

    return [document.id for document in pending if ocr_for(session, service, document)]


def ocr_for(session: Session, service: WebDavService, document: Document) -> bool:
    """OCR one document and cache the result. Returns True when a copy was produced.

    Like proposals, every failure lands on a status and a readable message rather than an
    exception: a document nobody could OCR is still filed by hand, just without a text layer.
    """
    try:
        data = b"".join(service.read_stream(document.webdav_path))
    except AppError as exc:
        _fail_ocr(session, document, f"Couldn't read the file: {exc.message}")
        return False

    extracted = extraction.extract(data, document.original_filename, document.mime_type)
    # Re-checked here rather than trusted from ingest. The migration that introduced this
    # column had to guess for documents already in the queue, and spending minutes adding a
    # text layer to a page that already has one is the one outcome worth a second read.
    if extracted.mime_type != extraction.PDF_MIME or extracted.has_usable_text:
        document.ocr_status = OcrStatus.not_needed
        document.ocr_error = None
        session.add(document)
        session.commit()
        return False

    ocr_started = time.monotonic()
    try:
        output = searchable.build(data)
    except searchable.SearchableUnavailable as exc:
        _fail_ocr(session, document, str(exc))
        return False
    finally:
        metrics.ocr_duration.observe(time.monotonic() - ocr_started)

    searchable.store(document.content_hash, output)
    document.ocr_status = OcrStatus.ready
    document.ocr_error = None
    # The document failed its proposal on "no text layer" at ingest, and that is no longer
    # true -- there is text now. Putting it back to pending is what lets the proposal phase
    # later in this same tick read it. Only when it failed: a proposal that already
    # succeeded is not improved by being thrown away.
    if document.proposal_status is ProposalStatus.failed:
        document.proposal_status = ProposalStatus.pending
        document.proposal_error = None
    session.add(document)
    audit.record(
        session,
        AuditAction.document_ocr_completed,
        actor=SYSTEM,
        document_id=document.id,
        summary=f"Made a searchable copy of {document.original_filename}",
        details={"size_bytes_before": document.file_size_bytes, "size_bytes_after": len(output)},
    )
    session.commit()
    metrics.ocr_runs.labels("ready").inc()

    logger.info(
        "OCR ready for %s (%d bytes -> %d).",
        document.webdav_path,
        document.file_size_bytes,
        len(output),
    )
    return True


def _fail_ocr(session: Session, document: Document, reason: str) -> None:
    document.ocr_status = OcrStatus.failed
    document.ocr_error = reason
    session.add(document)
    audit.record(
        session,
        AuditAction.document_ocr_failed,
        actor=SYSTEM,
        document_id=document.id,
        summary=f"Couldn't make {document.original_filename} searchable: {reason}",
        details={"reason": reason},
    )
    session.commit()
    metrics.ocr_runs.labels("failed").inc()
    logger.info("OCR failed for %s: %s", document.webdav_path, reason)


def generate_proposals(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    already_extracted: dict[str, ExtractedDocument] | None = None,
) -> tuple[int, int]:
    """Produce a proposal for documents still waiting for one. Returns (ready, failed)."""
    pending = session.exec(
        select(Document)
        .where(Document.user_id == app_settings.user_id)
        .where(Document.proposal_status == ProposalStatus.pending)
        # Filed documents keep their proposal_status until they leave the queue; without
        # this a document approved while its proposal was still queued would burn an LLM
        # call and attach a proposal to an already-filed document.
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
        .order_by(Document.discovered_at)  # type: ignore[arg-type]
        .limit(app_settings.proposal_batch_size)
    ).all()

    cache = already_extracted or {}
    readings = _read_all(session, service, app_settings, pending, cache)
    ready = 0
    for document in pending:
        with progress.tracking(document.id):
            progress.report("Starting the analysis…")
            if propose_for(
                session,
                service,
                app_settings,
                document,
                cache.get(document.id),
                prepared=readings.get(document.id),
            ):
                ready += 1
    return ready, len(pending) - ready


def _read_all(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    documents: Sequence[Document],
    cache: dict[str, ExtractedDocument],
) -> dict[str, tuple["_Reading", ai.UsageMeter]]:
    """Run the extraction task for the whole batch before any filing starts.

    Reading and filing usually use different models, and on one home GPU only one of them
    fits at a time: alternating per document would unload and reload both for every
    document. Done batch-wise, each model is loaded once per tick.
    """
    steps = ai_tasks.resolve_chain(session, app_settings.user_id, AiTask.extraction)
    if not steps or not app_settings.allowed_root_folders:
        return {}
    readings: dict[str, tuple[_Reading, ai.UsageMeter]] = {}
    to_read = [document for document in documents if not document.extracted_markdown]
    for document in to_read:
        meter = ai.UsageMeter()
        with progress.tracking(document.id):
            try:
                reading = _read_document(
                    session,
                    service,
                    document,
                    cache.get(document.id),
                    app_settings.extraction_instructions,
                    meter,
                )
            except AppError as exc:
                reading = _Reading(fatal=f"Couldn't read the file: {exc.message}")
        readings[document.id] = (reading, meter)
        # tracking() clears the stage on the way out; say why it is still waiting.
        progress.set_stage(document.id, "Read; waiting for the other documents to be read…")
    return readings


def propose_for(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    document: Document,
    already_extracted: ExtractedDocument | None = None,
    *,
    prepared: tuple["_Reading", ai.UsageMeter] | None = None,
) -> bool:
    """Generate and store one proposal. Returns True when a proposal was stored.

    `prepared` is a reading the batch already made (see _read_all), with the meter that
    counted it, so the document is not read twice and its usage covers both stages.

    Every failure path lands on `proposal_status=failed` with a readable message rather
    than an exception: the document stays approvable by hand either way.
    """
    if not app_settings.allowed_root_folders:
        _fail(session, document, "No allowed folders are configured yet — set them in Settings.")
        return False

    if prepared is not None:
        reading, meter = prepared
    else:
        # Every model call below, extraction and filing alike, reports what it cost here.
        meter = ai.UsageMeter()
        try:
            reading = _read_document(
                session,
                service,
                document,
                already_extracted,
                app_settings.extraction_instructions,
                meter,
            )
        except AppError as exc:
            _fail(session, document, f"Couldn't read the file: {exc.message}", meter)
            return False

    if reading.fatal is not None:
        _fail(session, document, reading.fatal, meter)
        return False

    tree, samples = folders.prompt_context(service, app_settings)
    prompt = ai.build_prompt(
        reading.text,
        tree,
        samples,
        app_settings.filename_pattern_hint,
        app_settings.filing_instructions,
    )
    allowed_roots = list(app_settings.allowed_root_folders)

    def file_it(step: ai_tasks.ResolvedStep) -> ai.Proposal:
        return ai.request_proposal(
            endpoint_url=step.endpoint_url,
            model_name=step.model_name,
            api_key=step.api_key,
            prompt=prompt,
            allowed_roots=allowed_roots,
            usage=step.usage(meter, AiTask.filing),
            timeout=step.timeout_seconds,
        )

    try:
        proposal = ai_tasks.run_chain(_filing_chain(session, document), file_it, AiTask.filing)
    except ai.ProposalRejected as exc:
        _fail(session, document, exc.reason, meter)
        return False
    except AppError as exc:
        _fail(session, document, exc.message, meter)
        return False

    proposal = ai.with_date_prefix(proposal, app_settings.filename_pattern)
    _store(session, document, proposal, prompt, meter)
    return True


def _filing_chain(session: Session, document: Document) -> list[ai_tasks.ResolvedStep]:
    """The configured chain, or the one connection and model picked for this run.

    The pick is spent here: cleared before the call so a failure does not replay it on every
    later tick, and the next plain re-run goes back to the configured chain.
    """
    endpoint_id, model_name = document.rerun_endpoint_id, document.rerun_model_name
    if endpoint_id is None or model_name is None:
        return ai_tasks.resolve_chain(session, document.user_id, AiTask.filing)
    document.rerun_endpoint_id = None
    document.rerun_model_name = None
    session.add(document)
    session.commit()
    return ai_tasks.resolve_single(session, document.user_id, endpoint_id, model_name)


@dataclass(frozen=True)
class _Reading:
    """The text handed to the filing model, or the reason there is none."""

    text: str = ""
    fatal: str | None = None


def _read_document(
    session: Session,
    service: WebDavService,
    document: Document,
    already_extracted: ExtractedDocument | None,
    instructions: str | None,
    meter: ai.UsageMeter,
) -> _Reading:
    """Markdown from the extraction task when it is configured, the text layer otherwise.

    A failed transcription is recorded and stepped over rather than raised: a worse reading
    of the document still files it, where a stopped pipeline leaves it sitting in the queue.
    """
    if document.extracted_markdown:
        return _Reading(_for_prompt(document.extracted_markdown))

    steps = ai_tasks.resolve_chain(session, document.user_id, AiTask.extraction)
    extracted = already_extracted

    if not steps:
        if extracted is None:
            # Only re-download when we did not just read this file (regenerate, or a
            # document carried over from an earlier tick).
            data = _document_bytes(service, document)
            extracted = extraction.extract(data, document.original_filename, document.mime_type)
        return _text_layer_reading(document, extracted)

    data = _document_bytes(service, document)
    if extracted is None:
        extracted = extraction.extract(data, document.original_filename, document.mime_type)

    try:
        transcription = transcribe.transcribe(data, steps, instructions, meter)
    except (AppError, extraction.RenderUnavailable) as exc:
        reason = exc.message if isinstance(exc, AppError) else str(exc)
        logger.warning("Extraction failed for %s: %s", document.webdav_path, reason)
        _record_extraction(session, document, markdown=None, model=None, error=reason)
        return _text_layer_reading(document, extracted)

    if not transcription.markdown:
        _record_extraction(
            session, document, markdown=None, model=None, error="The model returned no text."
        )
        return _text_layer_reading(document, extracted)

    _record_extraction(
        session,
        document,
        markdown=transcription.markdown,
        model=transcription.model_label,
        error=None,
    )
    return _Reading(_for_prompt(transcription.markdown))


def _text_layer_reading(document: Document, extracted: ExtractedDocument) -> _Reading:
    if extracted.text_error is not None:
        # The OCR failure's own reason when there is one: "This PDF is encrypted, so it
        # can't be OCR'd" tells the person something they can act on, where the generic
        # "no text layer" leaves them wondering why nothing happened.
        return _Reading(fatal=document.ocr_error or extracted.text_error)
    return _Reading(extracted.text)


def _for_prompt(markdown: str) -> str:
    """Markdown is stored whole for the review screen, but capped on the way into a prompt."""
    return markdown[: extraction.MAX_TEXT_CHARS]


def _record_extraction(
    session: Session,
    document: Document,
    *,
    markdown: str | None,
    model: str | None,
    error: str | None,
) -> None:
    document.extracted_markdown = markdown
    document.extraction_model = model
    document.extraction_error = error
    session.add(document)
    session.commit()


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


def _store(
    session: Session,
    document: Document,
    proposal: ai.Proposal,
    prompt: str,
    meter: ai.UsageMeter,
) -> None:
    """Replace any existing proposal wholesale (SPEC 4.1) inside one transaction."""
    existing = session.exec(select(Proposal).where(Proposal.document_id == document.id)).first()
    if existing is not None:
        session.delete(existing)
        # Flush so the DELETE reaches the database before the INSERT below. Without this
        # SQLAlchemy can order the INSERT first within a single flush, which trips the
        # unique constraint on proposal.document_id. Still one transaction: a failure
        # here rolls the delete back too.
        session.flush()

    session.add(
        Proposal(
            document_id=document.id,
            suggested_name=proposal.suggested_name,
            target_folder_path=proposal.target_folder_path,
            document_date=proposal.document_date,
            confidence_score=proposal.confidence_score,
            reasoning_text=proposal.reasoning_text,
            model_name=proposal.model_name,
            created_at=to_storage(utc_now()),
            prompt_text=prompt,
        )
    )
    usage = meter.summary()
    document.proposal_status = ProposalStatus.ready
    document.proposal_error = None
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
            "filing_model": proposal.model_name,
            "extraction_model": document.extraction_model,
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
    session: Session, document: Document, reason: str, meter: ai.UsageMeter | None = None
) -> None:
    """Also replaces the stored usage: it describes the latest analysis, and this was it --
    including the tokens a rejected reply cost, and None when no model was reached at all."""
    usage = meter.summary() if meter else None
    document.proposal_status = ProposalStatus.failed
    document.proposal_error = reason
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
            "extraction_model": document.extraction_model,
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


def _usage_suffix(usage: ai.UsageSummary | None) -> str:
    return f" ({usage.describe()})" if usage else ""
