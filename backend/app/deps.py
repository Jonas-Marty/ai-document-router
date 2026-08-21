from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.db import get_session

SessionDep = Annotated[Session, Depends(get_session)]


class CurrentUser:
    id: str = "single-user"


# AUTH: replace with real JWT validation for Authentik. Every non-health route depends on
# this; nothing else in the codebase should need to change when auth is added.
def get_current_user() -> CurrentUser:
    return CurrentUser()


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
