from typing import Annotated

from fastapi import Cookie, Depends
from sqlmodel import Session

from app.db import get_session
from app.models import AppSettings, User
from app.services import auth as auth_service
from app.services import settings as settings_service
from app.services.errors import AdminRequired
from app.services.webdav import WebDavService, build_client

SessionDep = Annotated[Session, Depends(get_session)]


def get_current_user(
    session: SessionDep,
    session_cookie: Annotated[str | None, Cookie(alias=auth_service.SESSION_COOKIE_NAME)] = None,
) -> User:
    """The signed-in user, or 401. Every route except /health and /auth/* depends on this."""
    return auth_service.resolve_session(session, session_cookie)


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_admin_user(user: CurrentUserDep) -> User:
    if not user.is_admin:
        raise AdminRequired("This action needs an admin account.")
    return user


AdminUserDep = Annotated[User, Depends(get_admin_user)]


def get_app_settings(session: SessionDep, user: CurrentUserDep) -> AppSettings:
    """The signed-in user's settings. Depending on this implies being signed in."""
    return settings_service.get_settings(session, user.id)


AppSettingsDep = Annotated[AppSettings, Depends(get_app_settings)]


def get_webdav(session: SessionDep, app_settings: AppSettingsDep) -> WebDavService:
    """A WebDAV service on the signed-in user's own Nextcloud, scoped to their settings.

    Built per request rather than once at import: the roots and the connection are editable
    at runtime, and a service frozen at startup would keep enforcing a stale set of roots.
    409 with a pointer to Settings when the user has not connected a Nextcloud yet.

    Note this carries the *permitted* roots (allowed + trash + watch + the folders of open
    documents), which is the I/O backstop. Anything acting on a user-supplied destination
    must additionally check against allowed_root_folders alone -- see router.approve.
    """
    connection = settings_service.require_connection(app_settings)
    return WebDavService(
        build_client(connection),
        settings_service.permitted_roots(session, app_settings),
        cache_namespace=connection.cache_namespace,
    )


WebDavDep = Annotated[WebDavService, Depends(get_webdav)]
