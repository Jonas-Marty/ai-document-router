from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.db import get_session
from app.models import AppSettings
from app.services import settings as settings_service
from app.services.errors import NotFoundError
from app.services.webdav import WebDavService, build_client

SessionDep = Annotated[Session, Depends(get_session)]


def get_webdav(session: SessionDep) -> WebDavService:
    """A WebDAV service scoped to the *current* settings.

    Built per request rather than once at import: allowed_root_folders is editable at
    runtime, and a service frozen at startup would keep enforcing a stale set of roots.
    """
    app_settings = session.get(AppSettings, 1)
    if app_settings is None:
        raise NotFoundError("Settings have not been initialised.")
    return WebDavService(build_client(), settings_service.permitted_roots(app_settings))


WebDavDep = Annotated[WebDavService, Depends(get_webdav)]


class CurrentUser:
    id: str = "single-user"


# AUTH: replace with real JWT validation for Authentik. Every non-health route depends on
# this; nothing else in the codebase should need to change when auth is added.
def get_current_user() -> CurrentUser:
    return CurrentUser()


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
