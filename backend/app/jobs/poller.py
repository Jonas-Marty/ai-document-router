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
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app import db, log
from app.config import settings as config
from app.models import (
    AppSettings,
    AuditAction,
    Document,
    DocumentStatus,
    OcrStatus,
    ProposalStatus,
)
from app.services import analysis, audit, extraction, metrics, searchable
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
            ready, failed = analysis.run_batch(session, service, app_settings, fresh)
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
