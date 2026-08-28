import re
from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from sqlmodel import Session

from app.config import settings as config
from app.models import AppSettings
from app.schemas import SettingsRead, SettingsUpdate
from app.services import ai, crypto
from app.services.errors import NotFoundError, ValidationError
from app.services.paths import is_within, normalize_path

_SETTINGS_ID = 1

_PRIVATE_NETWORKS = [
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),
]


def get_settings(session: Session) -> AppSettings:
    settings = session.get(AppSettings, _SETTINGS_ID)
    if settings is None:
        raise NotFoundError("Settings have not been initialised.")
    return settings


def permitted_roots(settings: AppSettings) -> list[str]:
    """Every path tree the app may touch: allowed roots plus the deliberate exceptions.

    Empties are dropped. On a freshly seeded database `trash_folder_path` is "" and
    `allowed_root_folders` is [], and `normalize_path("")` raises -- without this filter the
    poller's first tick would die in the WebDavService constructor before reaching WebDAV.
    """
    candidates = (
        *settings.allowed_root_folders,
        settings.trash_folder_path,
        config.webdav_watch_folder,
    )
    return [root for root in candidates if root]


def to_read_schema(settings: AppSettings) -> SettingsRead:
    return SettingsRead(
        allowed_root_folders=settings.allowed_root_folders,
        trash_folder_path=settings.trash_folder_path,
        filename_pattern=settings.filename_pattern,
        filename_pattern_hint=settings.filename_pattern_hint,
        ai_endpoint_url=settings.ai_endpoint_url,
        ai_model_name=settings.ai_model_name,
        ai_api_key_set=settings.ai_api_key_encrypted is not None,
    )


def update_settings(session: Session, payload: SettingsUpdate, secret_key: str) -> AppSettings:
    settings = get_settings(session)

    allowed_roots = _validate_allowed_roots(payload.allowed_root_folders)
    trash_folder = _validate_trash_folder(payload.trash_folder_path, allowed_roots)
    _validate_filename_pattern(payload.filename_pattern)
    _validate_ai_endpoint_url(payload.ai_endpoint_url)

    settings.allowed_root_folders = allowed_roots
    settings.trash_folder_path = trash_folder
    settings.filename_pattern = payload.filename_pattern
    settings.filename_pattern_hint = payload.filename_pattern_hint
    settings.ai_endpoint_url = payload.ai_endpoint_url
    settings.ai_model_name = payload.ai_model_name

    if payload.ai_api_key:
        settings.ai_api_key_encrypted = crypto.encrypt(secret_key, payload.ai_api_key)

    session.add(settings)
    session.commit()
    session.refresh(settings)
    return settings


def list_ai_models(session: Session, endpoint_url: str, api_key: str | None) -> list[str]:
    """Models offered by the endpoint the user is about to save.

    The URL comes from the form rather than the database so the button can be pressed before
    saving -- which is the point, since it is how you find out the URL is wrong. A blank key
    means "use the stored one": the form cannot send back a key it is never given (CLAUDE.md
    rule 5), so requiring one would make Test unusable on every visit after the first.
    """
    url = endpoint_url.strip()
    if not url:
        raise ValidationError("Add an AI endpoint URL before testing the connection.")
    _validate_ai_endpoint_url(url)

    key = api_key.strip() if api_key else ""
    if not key:
        settings = get_settings(session)
        if settings.ai_api_key_encrypted is not None:
            key = crypto.decrypt(config.secret_key, settings.ai_api_key_encrypted)

    return ai.list_models(endpoint_url=url, api_key=key or None)


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


def _validate_filename_pattern(pattern: str | None) -> None:
    if pattern is None:
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValidationError(f"Filename pattern is not a valid regex: {exc}") from exc


def _validate_ai_endpoint_url(url: str) -> None:
    # "" is the seeded not-configured-yet state (models.AppSettings), and every settings card
    # PUTs the whole object -- so rejecting it here would make the first save of Folders or
    # Naming on a fresh install fail on a field the user has not reached yet, with no way to
    # set that field first (saving the AI card would fail on the still-empty roots). Format is
    # checked only once a URL is actually present; the poller reports an unconfigured endpoint
    # per document instead. Only "" is the unconfigured state -- the frontend trims before
    # sending, so whitespace here is a malformed URL, not a cleared field.
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and _is_private_host(parsed.hostname):
        return
    raise ValidationError(
        "AI endpoint URL must use https://, or http:// for a private network host."
    )


def _is_private_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname == "localhost":
        return True
    try:
        addr = ip_address(hostname)
    except ValueError:
        return False
    return any(addr in network for network in _PRIVATE_NETWORKS)
