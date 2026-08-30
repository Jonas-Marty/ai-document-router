"""Authorization Code flow with PKCE against one configured OIDC provider (Authentik, etc).

The ID token's signature is deliberately not verified locally. OIDC Core 3.1.3.7 allows
skipping it when the token comes straight from the token endpoint over TLS to a client that
authenticated with its secret -- which is exactly this flow -- and doing it properly would
mean JWKS fetching, key rotation, and a JWT library this project does not otherwise need
(CLAUDE.md rule 8). Identity is read from /userinfo, a second authenticated call, rather
than from an unverified token body.
"""

import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlmodel import Session, select

from app.config import settings as config
from app.models import OidcLogin
from app.services.errors import OidcError

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10.0
LOGIN_TTL_SECONDS = 600
_DISCOVERY_TTL_SECONDS = 300

_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def callback_url() -> str:
    return f"{config.app_base_url.rstrip('/')}/api/v1/auth/oidc/callback"


def discover(client: httpx.Client | None = None) -> dict[str, Any]:
    """The provider's endpoints, cached briefly so a login burst is one request, not five."""
    issuer = config.oidc_issuer.rstrip("/")
    cached = _discovery_cache.get(issuer)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]

    url = f"{issuer}/.well-known/openid-configuration"
    document = _get_json(url, client=client, what="discovery document")
    for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        if not isinstance(document.get(key), str):
            raise OidcError(f"The provider's discovery document has no {key}.")

    _discovery_cache[issuer] = (time.monotonic() + _DISCOVERY_TTL_SECONDS, document)
    return document


def begin_login(session: Session, client: httpx.Client | None = None) -> str:
    """Record a pending flow and return the URL to send the browser to."""
    document = discover(client)
    _purge_expired(session)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    redirect_uri = callback_url()

    session.add(
        OidcLogin(
            state=state,
            code_verifier=verifier,
            nonce=nonce,
            redirect_uri=redirect_uri,
            created_at=datetime.now(UTC),
        )
    )
    session.commit()

    query = httpx.QueryParams(
        {
            "response_type": "code",
            "client_id": config.oidc_client_id,
            "redirect_uri": redirect_uri,
            "scope": config.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{document['authorization_endpoint']}?{query}"


def complete_login(
    session: Session, code: str, state: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Exchange the code and return the provider's claims about the user.

    The pending row is consumed whatever happens next: a state is single-use, so a replayed
    callback finds nothing and is rejected.
    """
    pending = session.get(OidcLogin, state)
    if pending is None:
        raise OidcError("This sign-in link has expired or was already used. Try again.")
    session.delete(pending)
    session.commit()

    if _as_utc(pending.created_at) + timedelta(seconds=LOGIN_TTL_SECONDS) < datetime.now(UTC):
        raise OidcError("This sign-in took too long. Try again.")

    document = discover(client)
    tokens = _post_form(
        document["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "client_id": config.oidc_client_id,
            "client_secret": config.oidc_client_secret,
            "code_verifier": pending.code_verifier,
        },
        client=client,
    )

    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OidcError("The provider's token response had no access token.")

    claims = _get_json(
        document["userinfo_endpoint"],
        client=client,
        what="userinfo response",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(subject, str) or not subject:
        raise OidcError("The provider did not return a subject for this user.")
    if not isinstance(email, str) or "@" not in email:
        raise OidcError(
            "The provider did not return an email address. Add the 'email' scope to the "
            "application, or map an email claim onto it."
        )
    return {"sub": subject, "email": email}


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _purge_expired(session: Session) -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=LOGIN_TTL_SECONDS)
    stale = session.exec(select(OidcLogin).where(OidcLogin.created_at < cutoff)).all()
    for row in stale:
        session.delete(row)
    session.commit()


def _get_json(
    url: str,
    *,
    client: httpx.Client | None,
    what: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        try:
            response = http.get(url, headers=headers or {})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("OIDC GET %s failed: %s", url, exc)
            raise OidcError(f"Couldn't reach the identity provider: {exc}.") from exc
        return _parse(response, url, what)
    finally:
        if owned:
            http.close()


def _post_form(url: str, form: dict[str, str], *, client: httpx.Client | None) -> dict[str, Any]:
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        try:
            response = http.post(url, data=form)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("OIDC POST %s failed: %s", url, exc)
            raise OidcError(f"Couldn't reach the identity provider: {exc}.") from exc
        return _parse(response, url, "token response")
    finally:
        if owned:
            http.close()


def _parse(response: httpx.Response, url: str, what: str) -> dict[str, Any]:
    if response.status_code >= 400:
        # The body can carry the provider's own reason (invalid_client, bad redirect URI),
        # which is the difference between a fixable message and "something went wrong".
        logger.warning("OIDC %s from %s: %s", response.status_code, url, response.text[:500])
        raise OidcError(f"The identity provider returned {response.status_code} for the {what}.")
    try:
        parsed = json.loads(response.text)
    except ValueError as exc:
        raise OidcError(f"The identity provider's {what} was not JSON.") from exc
    if not isinstance(parsed, dict):
        raise OidcError(f"The identity provider's {what} had an unexpected shape.")
    return parsed


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
