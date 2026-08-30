from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Response
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.config import settings as app_config
from app.deps import CurrentUserDep, SessionDep
from app.models import User
from app.schemas import AuthConfig, CredentialsRequest, UserRead
from app.services import auth as auth_service
from app.services import oidc as oidc_service
from app.services.errors import OidcError

router = APIRouter()

SessionCookie = Annotated[str | None, Cookie(alias=auth_service.SESSION_COOKIE_NAME)]


# AUTH: everything in this router except /auth/me is deliberately unauthenticated -- it is
# how someone becomes authenticated. Nothing here may say more about the instance than the
# sign-in screen has to render.
@router.get("/auth/config")
def read_auth_config(session: SessionDep) -> AuthConfig:
    return AuthConfig(
        oidc_enabled=app_config.oidc_enabled,
        oidc_provider_name=app_config.oidc_provider_name,
        registration_open=auth_service.registration_open(session),
        has_users=auth_service.user_count(session) > 0,
    )


@router.get("/auth/me")
def read_me(user: CurrentUserDep) -> UserRead:
    return _to_read(user)


@router.post("/auth/register", status_code=201)
def register(payload: CredentialsRequest, session: SessionDep, response: Response) -> UserRead:
    user = auth_service.register(session, payload.email, payload.password)
    _set_session_cookie(response, auth_service.start_session(session, user))
    return _to_read(user)


@router.post("/auth/login")
def login(payload: CredentialsRequest, session: SessionDep, response: Response) -> UserRead:
    user = auth_service.authenticate(session, payload.email, payload.password)
    _set_session_cookie(response, auth_service.start_session(session, user))
    return _to_read(user)


@router.post("/auth/logout", status_code=204)
def logout(session: SessionDep, response: Response, session_cookie: SessionCookie = None) -> None:
    auth_service.end_session(session, session_cookie)
    response.delete_cookie(
        auth_service.SESSION_COOKIE_NAME, path="/", samesite="lax", httponly=True
    )


@router.get("/auth/oidc/login")
def oidc_login(session: SessionDep) -> RedirectResponse:
    if not app_config.oidc_enabled:
        raise OidcError("Single sign-on is not configured on this instance.")
    # 303: the browser followed a link, and what comes back is a page, not this resource.
    return RedirectResponse(oidc_service.begin_login(session), status_code=303)


@router.get("/auth/oidc/callback")
def oidc_callback(
    session: SessionDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """Where the provider sends the browser back.

    Failures land on the sign-in screen with a reason in the query string: a JSON error
    envelope rendered in an address bar helps nobody.
    """
    if not app_config.oidc_enabled:
        raise OidcError("Single sign-on is not configured on this instance.")
    if error:
        return _back_to_login(error_description or error)
    if not code or not state:
        return _back_to_login("The identity provider sent an incomplete response.")

    try:
        claims = oidc_service.complete_login(session, code, state)
        user = _link_or_create(session, claims["sub"], claims["email"])
    except OidcError as exc:
        return _back_to_login(exc.message)

    response = RedirectResponse(app_config.app_base_url.rstrip("/") + "/", status_code=303)
    _set_session_cookie(response, auth_service.start_session(session, user))
    return response


def _link_or_create(session: Session, subject: str, email: str) -> User:
    """Match on subject first, then on email.

    The subject is the stable identity -- an address can be reassigned at the provider. The
    email fallback is what lets whoever registered with a password keep one account when
    they later sign in through the provider, instead of silently ending up with two.
    """
    address = auth_service.normalize_email(email)

    user = session.exec(select(User).where(User.oidc_subject == subject)).first()
    if user is None:
        user = session.exec(select(User).where(User.email == address)).first()

    if user is None:
        user = User(
            email=address,
            oidc_subject=subject,
            # Same rule as password registration: whoever gets here first owns the instance.
            is_admin=auth_service.user_count(session) == 0,
            created_at=datetime.now(UTC),
        )
    else:
        user.oidc_subject = subject
        user.email = address

    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _back_to_login(reason: str) -> RedirectResponse:
    base = app_config.app_base_url.rstrip("/")
    return RedirectResponse(f"{base}/login?error={quote(reason)}", status_code=303)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth_service.SESSION_COOKIE_NAME,
        token,
        max_age=app_config.session_lifetime_days * 24 * 60 * 60,
        httponly=True,
        # Lax, not Strict: the OIDC callback is a cross-site redirect back into this app, and
        # Strict would withhold the cookie on exactly that navigation.
        samesite="lax",
        secure=app_config.session_cookie_secure,
        path="/",
    )


def _to_read(user: User) -> UserRead:
    return UserRead(id=user.id, email=user.email, is_admin=user.is_admin)
