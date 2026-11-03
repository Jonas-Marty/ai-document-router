import logging
import re

from sqlmodel import Session, select

from app.config import settings as config
from app.models import AppSettings, AuditAction, Document, DocumentStatus
from app.schemas import NextcloudRead, NextcloudWrite, SettingsRead, SettingsUpdate
from app.services import audit, crypto, urls, webdav
from app.services.audit import Actor
from app.services.errors import AppError, ValidationError
from app.services.paths import is_within, normalize_path
from app.services.webdav import Connection, parent_of

logger = logging.getLogger(__name__)

_LABELS = {
    "allowed_root_folders": "allowed folders",
    "trash_folder_path": "trash folder",
    "watch_folder_path": "watch folder",
    "filename_pattern": "filename pattern",
    "filename_pattern_hint": "pattern hint",
    "store_ocr_text": "searchable copies",
    "filing_instructions": "filing instructions",
    "extraction_instructions": "extraction instructions",
    "proposal_batch_size": "batch size",
}
MAX_INSTRUCTIONS_CHARS = 2000
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 50


class NextcloudNotConfigured(AppError):
    """This user has not connected a Nextcloud yet, so there is nothing to read or file."""

    code = "nextcloud_not_configured"
    status_code = 409


def get_settings(session: Session, user_id: str) -> AppSettings:
    """This user's settings, created empty the first time they are asked for."""
    settings = session.exec(select(AppSettings).where(AppSettings.user_id == user_id)).first()
    if settings is None:
        settings = AppSettings(user_id=user_id)
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def connection_for(settings: AppSettings) -> Connection | None:
    """The user's Nextcloud with the password decrypted, or None until all of it is set."""
    if not (
        settings.webdav_base_url and settings.webdav_username and settings.webdav_password_encrypted
    ):
        return None
    return Connection(
        base_url=settings.webdav_base_url,
        username=settings.webdav_username,
        password=crypto.decrypt(config.secret_key, settings.webdav_password_encrypted),
    )


def require_connection(settings: AppSettings) -> Connection:
    connection = connection_for(settings)
    if connection is None:
        raise NextcloudNotConfigured(
            "Connect your Nextcloud in Settings first: server URL, username and app password."
        )
    return connection


def read_nextcloud(settings: AppSettings) -> NextcloudRead:
    """Never the password -- whether one is stored, and nothing else (CLAUDE.md rule 5)."""
    return NextcloudRead(
        base_url=settings.webdav_base_url,
        username=settings.webdav_username,
        password_set=settings.webdav_password_encrypted is not None,
    )


def update_nextcloud(
    session: Session, settings: AppSettings, payload: NextcloudWrite, *, actor: Actor
) -> AppSettings:
    """A blank password keeps the stored one -- the form is never given it back to resend."""
    base_url = _validate_nextcloud_url(payload.base_url)
    username = payload.username.strip()
    if not username:
        raise ValidationError("Enter your Nextcloud username.")
    password = payload.password or ""
    if not password and settings.webdav_password_encrypted is None:
        raise ValidationError("Enter an app password. Create one in Nextcloud under Security.")

    before = {"base_url": settings.webdav_base_url, "username": settings.webdav_username}
    settings.webdav_base_url = base_url
    settings.webdav_username = username
    if password:
        settings.webdav_password_encrypted = crypto.encrypt(config.secret_key, password)
    after = {"base_url": base_url, "username": username}

    session.add(settings)
    audit.record(
        session,
        AuditAction.settings_updated,
        actor=actor,
        summary="Changed the Nextcloud connection",
        details={
            "changed": {
                field: {"from": before[field], "to": after[field]}
                for field in before
                if before[field] != after[field]
            },
            # That it changed, and nothing else about it (rule 5).
            "password_changed": bool(password),
        },
    )
    session.commit()
    session.refresh(settings)
    return settings


def test_nextcloud(settings: AppSettings, payload: NextcloudWrite) -> None:
    """Sign in with what is in the form -- typed, not saved, since finding out it is wrong
    is the point. A blank password means the stored one."""
    password = payload.password or ""
    if not password:
        stored = connection_for(settings)
        password = stored.password if stored else ""
    if not password:
        raise ValidationError("Enter an app password to test.")
    webdav.check_connection(
        Connection(
            base_url=_validate_nextcloud_url(payload.base_url),
            username=payload.username.strip(),
            password=password,
        )
    )


def _validate_nextcloud_url(url: str) -> str:
    return urls.validate_service_url(url, "Nextcloud URL").rstrip("/")


def permitted_roots(session: Session, settings: AppSettings) -> list[str]:
    """Every path tree the app may touch: allowed roots plus the deliberate exceptions.

    Empties are dropped. On a freshly seeded database `trash_folder_path` is "" and
    `allowed_root_folders` is [], and `normalize_path("")` raises -- without this filter the
    poller's first tick would die in the WebDavService constructor before reaching WebDAV.

    The folders of documents still open in the queue are included too. After the watch
    folder changes in Settings, documents ingested from the old one must stay approvable,
    skippable and trashable where they are. These paths are trusted because only the poller
    writes them, from a listing of whatever was the watch folder at the time.
    """
    open_paths = session.exec(
        select(Document.webdav_path)
        .where(Document.user_id == settings.user_id)
        .where(
            Document.status.in_((DocumentStatus.pending, DocumentStatus.skipped))  # type: ignore[attr-defined]
        )
    ).all()
    candidates = (
        *settings.allowed_root_folders,
        settings.trash_folder_path,
        settings.watch_folder_path,
        *sorted({parent_of(normalize_path(path)) for path in open_paths}),
    )
    return [root for root in candidates if root]


def to_read_schema(settings: AppSettings) -> SettingsRead:
    return SettingsRead(
        allowed_root_folders=settings.allowed_root_folders,
        trash_folder_path=settings.trash_folder_path,
        watch_folder_path=settings.watch_folder_path,
        filename_pattern=settings.filename_pattern,
        filename_pattern_hint=settings.filename_pattern_hint,
        store_ocr_text=settings.store_ocr_text,
        filing_instructions=settings.filing_instructions,
        extraction_instructions=settings.extraction_instructions,
        proposal_batch_size=settings.proposal_batch_size,
    )


def update_settings(
    session: Session, settings: AppSettings, payload: SettingsUpdate, *, actor: Actor
) -> AppSettings:

    allowed_roots = _validate_allowed_roots(payload.allowed_root_folders)
    trash_folder = _validate_trash_folder(payload.trash_folder_path, allowed_roots)
    watch_folder = _validate_watch_folder(payload.watch_folder_path, allowed_roots, trash_folder)
    _validate_filename_pattern(payload.filename_pattern)

    updated = {
        "allowed_root_folders": allowed_roots,
        "trash_folder_path": trash_folder,
        "watch_folder_path": watch_folder,
        "filename_pattern": payload.filename_pattern,
        "filename_pattern_hint": payload.filename_pattern_hint,
        "store_ocr_text": payload.store_ocr_text,
        "filing_instructions": _validate_instructions(payload.filing_instructions, "Filing"),
        "extraction_instructions": _validate_instructions(
            payload.extraction_instructions, "Extraction"
        ),
        "proposal_batch_size": _validate_batch_size(payload.proposal_batch_size),
    }
    changed = {
        field: {"from": getattr(settings, field), "to": value}
        for field, value in updated.items()
        if getattr(settings, field) != value
    }
    for field, value in updated.items():
        setattr(settings, field, value)

    session.add(settings)
    audit.record(
        session,
        AuditAction.settings_updated,
        actor=actor,
        summary=(
            f"Changed {', '.join(_LABELS[field] for field in changed)}"
            if changed
            else "Saved settings without changing anything"
        ),
        # Old and new values in full: nothing in app_settings is a secret, and "what was the
        # trash folder before" is exactly what someone reading this later wants to know.
        details={"changed": changed},
    )
    session.commit()
    session.refresh(settings)
    logger.info("Settings saved; changed: %s", ", ".join(changed) or "nothing")
    return settings


def _validate_allowed_roots(roots: list[str]) -> list[str]:
    if not roots:
        raise ValidationError("At least one allowed root folder is required.")
    try:
        normalized = [normalize_path(root) for root in roots]
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if len(set(normalized)) != len(normalized):
        raise ValidationError("Allowed root folders must not contain duplicates.")
    for a in normalized:
        for b in normalized:
            if a != b and is_within(a, b):
                raise ValidationError(f"'{a}' must not be a prefix of '{b}'.")
    return normalized


def _validate_trash_folder(trash: str, allowed_roots: list[str]) -> str:
    try:
        normalized = normalize_path(trash)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    for root in allowed_roots:
        if is_within(root, normalized):
            raise ValidationError("Trash folder must not be inside any allowed root folder.")
    return normalized


def _validate_watch_folder(watch: str, allowed_roots: list[str], trash: str) -> str:
    """The watch folder may sit inside an allowed root (a common layout), but not be one or
    contain one: the poller ingests every file directly in it, so filed documents landing
    there would come straight back into the queue."""
    if not watch.strip():
        raise ValidationError("A watch folder is required: the folder your scanner saves into.")
    try:
        normalized = normalize_path(watch)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    for root in allowed_roots:
        if is_within(normalized, root):
            raise ValidationError("Watch folder must not be an allowed root folder or contain one.")
    if is_within(trash, normalized) or is_within(normalized, trash):
        raise ValidationError("Watch folder and trash folder must not overlap.")
    return normalized


def _validate_instructions(text: str | None, label: str) -> str | None:
    """Blank means none. Capped because every character is sent with every document, and a
    pasted essay would quietly crowd the document itself out of a small model's context."""
    trimmed = (text or "").strip()
    if len(trimmed) > MAX_INSTRUCTIONS_CHARS:
        raise ValidationError(
            f"{label} instructions must be {MAX_INSTRUCTIONS_CHARS} characters or fewer."
        )
    return trimmed or None


def _validate_batch_size(size: int) -> int:
    """Capped so one tick cannot hold the scheduler for an hour of local-model work."""
    if not MIN_BATCH_SIZE <= size <= MAX_BATCH_SIZE:
        raise ValidationError(
            f"Batch size must be between {MIN_BATCH_SIZE} and {MAX_BATCH_SIZE} documents."
        )
    return size


def _validate_filename_pattern(pattern: str | None) -> None:
    if pattern is None:
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValidationError(f"Filename pattern is not a valid regex: {exc}") from exc
