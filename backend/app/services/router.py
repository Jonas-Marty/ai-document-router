"""Approve, skip, trash, and revert -- SPEC 6.4.

Ordering is the whole design here. The WebDAV move happens *first*, and only once it has
succeeded does anything get written to the database. SPEC 6.4 step 6: if the move fails,
nothing is written and the document stays pending, so the user's edits survive in their
browser and they can retry. The reverse order would mark a document filed that never moved.
"""

import logging
from datetime import date

from sqlmodel import Session, select

from app.models import (
    AppSettings,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    OcrStatus,
    Proposal,
)
from app.services import naming, searchable
from app.services.documents import get_document
from app.services.errors import (
    AppError,
    FilenameCollision,
    NotRevertible,
    ValidationError,
    WebDAVConflict,
)
from app.services.extraction import extension_of, sha256
from app.services.paths import assert_within_allowed_roots, normalize_path
from app.services.times import to_storage, utc_now
from app.services.webdav import WebDavService, parent_of

logger = logging.getLogger(__name__)


def approve(
    session: Session,
    webdav: WebDavService,
    app_settings: AppSettings,
    document_id: str,
    final_name: str,
    final_folder_path: str,
    document_date: date | None,
) -> tuple[Document, HistoryEntry]:
    """File a document under a new name. Synchronous -- the user is waiting."""
    document = _require_queued(session, document_id)

    stem = naming.validate_stem(final_name)
    # Checked against allowed_root_folders specifically, NOT the WebDAV service's wider
    # permitted set. That set also contains the trash and watch folders, and approving into
    # the watch folder would have the poller re-ingest the file on its next tick, putting
    # the document back in the queue forever. SPEC 7.2.
    folder = assert_within_allowed_roots(final_folder_path, app_settings.allowed_root_folders)

    extension = extension_of(document.original_filename)
    destination = normalize_path(f"{folder}/{stem}{extension}")
    source = normalize_path(document.webdav_path)
    source_folder = parent_of(source)

    webdav.mkdir_p(folder)

    # SPEC 6.4 step 3: re-check immediately before the move. exists() is deliberately
    # uncached so this cannot be answered from a stale listing.
    if webdav.exists(destination):
        raise FilenameCollision(
            f"'{stem}{extension}' already exists in {folder}. Choose a different name."
        )

    try:
        webdav.move(source, destination)
    except WebDAVConflict as exc:
        # Overwrite: F is the real guarantee; the check above is just the nicer message.
        # Reported with the collision code so the form's blocking state is keyed the same
        # way whether the clash appeared before or after the check.
        raise FilenameCollision(
            f"'{stem}{extension}' already exists in {folder}. Choose a different name."
        ) from exc

    _store_searchable_copy(webdav, app_settings, document, destination)

    entry = _record_history(
        session,
        document=document,
        final_filename=f"{stem}{extension}",
        final_folder_path=folder,
        source_folder_path=source_folder,
        action=HistoryAction.moved,
        document_date=document_date,
    )
    document.webdav_path = destination
    document.status = DocumentStatus.moved
    session.add(document)
    session.commit()
    session.refresh(document)
    session.refresh(entry)

    logger.info("Approved %s -> %s", source, destination)
    return document, entry


def _store_searchable_copy(
    webdav: WebDavService,
    app_settings: AppSettings,
    document: Document,
    destination: str,
) -> None:
    """Replace the file we just filed with its OCR'd copy, when one is waiting.

    Order matters as much here as it does for the move above. The MOVE happens first and
    unchanged -- same collision check, same Overwrite: F -- so the only file this ever
    writes over is the one we put at `destination` moments ago. That is why it is a replace
    of our own file rather than an upload to a path that may hold someone else's, and why
    CLAUDE.md rule 1 is untouched: nothing is deleted, and PUT-then-delete-the-original was
    never on the table.

    It also cannot fail the approve. By this point the document is correctly filed; losing
    a text layer is not a reason to tell someone their filing did not happen, and what stays
    behind on failure is exactly what would have been filed before this existed.
    """
    if not app_settings.store_ocr_text or document.ocr_status is not OcrStatus.ready:
        return

    data = searchable.load(document.content_hash)
    if data is None:
        logger.info("No searchable copy cached for %s; filed the original.", document.id)
        return

    try:
        webdav.replace(destination, data, document.mime_type)
    except AppError as exc:
        logger.warning("Couldn't store the searchable copy at %s: %s", destination, exc.message)
        return

    # Discarded before content_hash is reassigned below -- the cache is keyed by the hash of
    # the *original* bytes, and dropping it afterwards would look for a file that never
    # existed and quietly leave the real entry behind.
    searchable.discard(document.content_hash)

    # The record now describes the file that is actually there. content_hash in particular:
    # the poller dedupes queued documents by it, and a hash of bytes no longer on the server
    # would make a re-ingest of this document look like a new one.
    document.content_hash = sha256(data)
    document.file_size_bytes = len(data)
    # Accurate rather than merely tidy -- the filed document has a text layer now, so if it
    # is reverted back into the queue there is nothing left to OCR.
    document.ocr_status = OcrStatus.not_needed
    logger.info("Filed %s with its OCR text layer.", destination)


def skip(session: Session, document_id: str) -> Document:
    """Send a document to the back of the queue. Touches nothing on the server."""
    document = _require_queued(session, document_id)
    document.status = DocumentStatus.skipped
    document.skip_count += 1
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def trash(
    session: Session,
    webdav: WebDavService,
    app_settings: AppSettings,
    document_id: str,
) -> tuple[Document, HistoryEntry]:
    """Move a document to the trash folder. Never deletes -- CLAUDE.md rule 1."""
    document = _require_queued(session, document_id)

    if not app_settings.trash_folder_path:
        raise ValidationError("No trash folder is configured yet — set one in Settings.")

    folder = normalize_path(app_settings.trash_folder_path)
    source = normalize_path(document.webdav_path)
    source_folder = parent_of(source)
    filename = document.original_filename

    webdav.mkdir_p(folder)

    # SPEC 6.4: on a name collision in trash, suffix with a timestamp rather than failing.
    # Trashing is a cleanup action; making the user rename something they are discarding
    # would be a strange thing to demand.
    destination = normalize_path(f"{folder}/{filename}")
    if webdav.exists(destination):
        stem, extension = _split_extension(filename)
        stamp = utc_now().strftime("%Y%m%d-%H%M%S")
        filename = f"{stem}_{stamp}{extension}"
        destination = normalize_path(f"{folder}/{filename}")

    webdav.move(source, destination)

    entry = _record_history(
        session,
        document=document,
        # The name actually written, not the one we intended: revert looks the file up by
        # this, and storing the un-suffixed name would make a revertible document look gone.
        final_filename=filename,
        final_folder_path=folder,
        source_folder_path=source_folder,
        action=HistoryAction.trashed,
        document_date=None,
    )
    # Nobody is going to file this, so the cached searchable copy has no claimant left.
    # Dropping it is cache housekeeping, not the WebDAV delete rule 1 forbids -- the
    # document itself is in the trash folder, intact.
    searchable.discard(document.content_hash)

    document.webdav_path = destination
    document.status = DocumentStatus.trashed
    session.add(document)
    session.commit()
    session.refresh(document)
    session.refresh(entry)

    logger.info("Trashed %s -> %s", source, destination)
    return document, entry


def revert(
    session: Session, webdav: WebDavService, history_id: str
) -> tuple[HistoryEntry, Document]:
    """Put a filed document back where it came from (SPEC 6.4)."""
    entry = session.get(HistoryEntry, history_id)
    if entry is None:
        raise NotRevertible("That history entry no longer exists.")
    if not entry.revertible:
        raise NotRevertible("That document has already been reverted.")

    document = session.get(Document, entry.document_id)
    if document is None:
        raise NotRevertible("The document behind that history entry no longer exists.")

    current = normalize_path(f"{entry.final_folder_path}/{entry.final_filename}")
    destination = normalize_path(f"{entry.source_folder_path}/{entry.original_filename}")

    if not webdav.exists(current):
        # SPEC 6.4: flip revertible off so the UI stops offering an action that cannot work.
        entry.revertible = False
        session.add(entry)
        session.commit()
        raise NotRevertible("That file isn't where it was filed any more, so it can't be put back.")

    # webdav_path is unique. A different document already sitting at the original path
    # would otherwise surface as an IntegrityError 500 instead of a readable conflict.
    clash = session.exec(
        select(Document).where(Document.webdav_path == destination, Document.id != document.id)
    ).first()
    if clash is not None:
        raise NotRevertible(
            f"Another document is already tracked at {destination}, so this can't be put back."
        )

    webdav.move(current, destination)

    document.webdav_path = destination
    document.status = DocumentStatus.pending
    document.skip_count = 0
    entry.revertible = False
    session.add(document)
    session.add(entry)
    session.commit()
    session.refresh(document)
    session.refresh(entry)

    logger.info("Reverted %s -> %s", current, destination)
    return entry, document


def _require_queued(session: Session, document_id: str) -> Document:
    document = get_document(session, document_id)
    if document.status not in (DocumentStatus.pending, DocumentStatus.skipped):
        raise ValidationError("That document has already been filed.")
    return document


def _split_extension(filename: str) -> tuple[str, str]:
    extension = extension_of(filename)
    if not extension:
        return filename, ""
    return filename[: -len(extension)], filename[-len(extension) :]


def _record_history(
    session: Session,
    *,
    document: Document,
    final_filename: str,
    final_folder_path: str,
    source_folder_path: str,
    action: HistoryAction,
    document_date: date | None,
) -> HistoryEntry:
    """Snapshot the AI's proposal and record what the user actually chose.

    `was_overridden` is computed here rather than accepted from the client: SPEC 5 is
    explicit that the client cannot be trusted to report what the AI said.
    """
    proposal = session.exec(select(Proposal).where(Proposal.document_id == document.id)).first()

    snapshot: dict[str, object] = {}
    overridden = False
    if proposal is not None:
        snapshot = {
            "suggested_name": proposal.suggested_name,
            "target_folder_path": proposal.target_folder_path,
            "document_date": (
                proposal.document_date.isoformat() if proposal.document_date else None
            ),
            "confidence_score": proposal.confidence_score,
            "reasoning_text": proposal.reasoning_text,
            "model_name": proposal.model_name,
        }
        if action is HistoryAction.moved:
            proposed_name = f"{proposal.suggested_name}{extension_of(document.original_filename)}"
            overridden = (
                final_filename != proposed_name
                # Both sides normalized: a trailing slash is not an override.
                or final_folder_path != normalize_path(proposal.target_folder_path)
                or document_date != proposal.document_date
            )
        # The user's corrected date belongs on the proposal, which revert keeps, so the
        # correction survives a revert. The snapshot above still holds what the AI said.
        if document_date != proposal.document_date:
            proposal.document_date = document_date
            session.add(proposal)

    entry = HistoryEntry(
        document_id=document.id,
        original_filename=document.original_filename,
        final_filename=final_filename,
        final_folder_path=final_folder_path,
        source_folder_path=source_folder_path,
        action=action,
        was_overridden=overridden,
        suggestion_snapshot=snapshot,
        processed_at=to_storage(utc_now()),
        revertible=True,
    )
    session.add(entry)
    return entry
