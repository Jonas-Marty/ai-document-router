"""Scan the watch folder and generate proposals.

SPEC 6.2 rule 4 is the important one: the poller only ever reads. It never moves or deletes
anything -- filing happens when the user approves, not here.

Both phases are capped per tick. A first run against a populated watch folder (the target
Nextcloud has 52 scans sitting in it) would otherwise download every file and make one LLM
call per document inside a single job run, while `max_instances=1` blocks the next tick.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app import db
from app.config import settings as config
from app.models import AppSettings, Document, DocumentStatus, Proposal, ProposalStatus
from app.services import ai, extraction, folders
from app.services import settings as settings_service
from app.services.documents import QUEUED_STATUSES
from app.services.errors import AppError
from app.services.extraction import ExtractedDocument
from app.services.times import to_storage, utc_now
from app.services.webdav import WebDavEntry, WebDavService, build_client

logger = logging.getLogger(__name__)

# SPEC 6.2 rule 3: extensions that mean "still being written".
PARTIAL_SUFFIXES = (".part", ".tmp", ".crdownload", ".filepart")

_scheduler: BackgroundScheduler | None = None


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
        id="poll_watch_folder",
    )
    _scheduler.start()
    logger.info("Poller started, every %ss.", config.poll_interval_seconds)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def run_once() -> None:
    """One full tick. Never raises -- a failing tick must not kill the scheduler."""
    try:
        with Session(db.engine) as session:
            app_settings = session.get(AppSettings, 1)
            if app_settings is None:
                logger.warning("Settings row missing; skipping tick.")
                return
            service = build_service(app_settings)
            # Carry the bytes we just read straight into the proposal step, so a document
            # ingested this tick is not downloaded a second time to extract the same text.
            fresh = ingest(session, service, app_settings)
            generate_proposals(session, service, app_settings, fresh)
    except Exception:
        logger.exception("Poller tick failed.")


def build_service(app_settings: AppSettings) -> WebDavService:
    return WebDavService(build_client(), settings_service.permitted_roots(app_settings))


def ingest(
    session: Session, service: WebDavService, app_settings: AppSettings
) -> dict[str, ExtractedDocument]:
    """Record new files in the watch folder.

    Returns what was extracted from each newly ingested document, keyed by document id, so
    the proposal step in the same tick can reuse it instead of re-downloading.
    """
    watch_folder = config.webdav_watch_folder
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
        if session.exec(select(Document).where(Document.webdav_path == entry.path)).first():
            continue
        recorded = _record(session, service, entry)
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
    session: Session, service: WebDavService, entry: WebDavEntry
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
            Document.content_hash == extracted.content_hash,
            Document.status.in_((DocumentStatus.pending, DocumentStatus.skipped)),  # type: ignore[attr-defined]
        )
    ).first()
    if duplicate is not None:
        logger.info("Skipping %s: same content as %s.", entry.path, duplicate.webdav_path)
        return None

    now = utc_now()
    document = Document(
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

    session.add(document)
    session.commit()
    logger.info("Ingested %s (%d bytes).", entry.path, extracted.file_size_bytes)
    return document, extracted


def generate_proposals(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    already_extracted: dict[str, ExtractedDocument] | None = None,
) -> int:
    """Produce a proposal for documents still waiting for one."""
    pending = session.exec(
        select(Document)
        .where(Document.proposal_status == ProposalStatus.pending)
        # Filed documents keep their proposal_status until they leave the queue; without
        # this a document approved while its proposal was still queued would burn an LLM
        # call and attach a proposal to an already-filed document.
        .where(Document.status.in_(QUEUED_STATUSES))  # type: ignore[attr-defined]
        .order_by(Document.discovered_at)  # type: ignore[arg-type]
        .limit(config.poller_proposal_batch)
    ).all()

    cache = already_extracted or {}
    done = 0
    for document in pending:
        if propose_for(session, service, app_settings, document, cache.get(document.id)):
            done += 1
    return done


def propose_for(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    document: Document,
    already_extracted: ExtractedDocument | None = None,
) -> bool:
    """Generate and store one proposal. Returns True when a proposal was stored.

    Every failure path lands on `proposal_status=failed` with a readable message rather
    than an exception: the document stays approvable by hand either way.
    """
    if not app_settings.allowed_root_folders:
        _fail(session, document, "No allowed folders are configured yet — set them in Settings.")
        return False

    extracted = already_extracted
    if extracted is None:
        # Only re-download when we did not just read this file (regenerate, or a document
        # carried over from an earlier tick).
        try:
            data = b"".join(service.read_stream(document.webdav_path))
        except AppError as exc:
            _fail(session, document, f"Couldn't read the file: {exc.message}")
            return False
        extracted = extraction.extract(data, document.original_filename, document.mime_type)

    if extracted.text_error is not None:
        _fail(session, document, extracted.text_error)
        return False

    tree, samples = folders.prompt_context(service, app_settings)
    prompt = ai.build_prompt(extracted.text, tree, samples, app_settings.filename_pattern_hint)

    try:
        proposal = ai.request_proposal(
            endpoint_url=app_settings.ai_endpoint_url,
            model_name=app_settings.ai_model_name,
            api_key=settings_service.decrypt_api_key(app_settings),
            prompt=prompt,
            allowed_roots=list(app_settings.allowed_root_folders),
        )
    except ai.ProposalRejected as exc:
        _fail(session, document, exc.reason)
        return False
    except AppError as exc:
        _fail(session, document, exc.message)
        return False

    _store(session, document, proposal)
    logger.info("Proposal ready for %s: %s", document.webdav_path, proposal.suggested_name)
    return True


def _store(session: Session, document: Document, proposal: ai.Proposal) -> None:
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
        )
    )
    document.proposal_status = ProposalStatus.ready
    document.proposal_error = None
    session.add(document)
    session.commit()


def _fail(session: Session, document: Document, reason: str) -> None:
    document.proposal_status = ProposalStatus.failed
    document.proposal_error = reason
    session.add(document)
    session.commit()
    logger.info("Proposal failed for %s: %s", document.webdav_path, reason)
