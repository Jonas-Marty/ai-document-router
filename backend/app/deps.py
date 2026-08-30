from typing import Annotated

from fastapi import Cookie, Depends
from sqlmodel import Session

from app.db import get_session
from app.models import AppSettings, User
from app.services import auth as auth_service
from app.services import settings as settings_service
from app.services.errors import AdminRequired, NotFoundError
from app.services.webdav import WebDavService, build_client

SessionDep = Annotated[Session, Depends(get_session)]


def get_app_settings(session: SessionDep) -> AppSettings:
    app_settings = session.get(AppSettings, 1)
    if app_settings is None:
        raise NotFoundError("Settings have not been initialised.")
    return app_settings


AppSettingsDep = Annotated[AppSettings, Depends(get_app_settings)]


def get_webdav(app_settings: AppSettingsDep) -> WebDavService:
    """A WebDAV service scoped to the *current* settings.

    Built per request rather than once at import: allowed_root_folders is editable at
    runtime, and a service frozen at startup would keep enforcing a stale set of roots.

    Note this carries the *permitted* roots (allowed + trash + watch), which is the I/O
    backstop. Anything acting on a user-supplied destination must additionally check
    against allowed_root_folders alone -- see router.approve.
    """
    return WebDavService(build_client(), settings_service.permitted_roots(app_settings))


WebDavDep = Annotated[WebDavService, Depends(get_webdav)]


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
