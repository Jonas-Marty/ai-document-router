"""Sign-in, registration, sessions, and the OIDC code flow.

The rule that shapes most of this: whoever registers first owns the instance and becomes
admin, and registration then closes unless it is explicitly reopened.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import db
from app.config import settings as config
from app.models import OidcLogin, User, UserSession
from app.services import auth as auth_service
from app.services import oidc as oidc_service
from app.services.errors import OidcError
from tests.conftest import TEST_EMAIL, TEST_PASSWORD

OTHER = {"email": "second@example.com", "password": "another-long-password"}


def register(client: TestClient, email: str, password: str) -> httpx.Response:
    return client.post("/api/v1/auth/register", json={"email": email, "password": password})


# --- passwords ---------------------------------------------------------------


def test_password_hash_round_trips_and_is_salted() -> None:
    first = auth_service.hash_password("correct-horse-battery-staple")
    second = auth_service.hash_password("correct-horse-battery-staple")

    assert first != second  # different salts, so a rainbow table is useless
    assert "correct-horse" not in first
    assert auth_service.verify_password("correct-horse-battery-staple", first)
    assert not auth_service.verify_password("wrong", first)


def test_verify_password_rejects_an_account_with_no_password() -> None:
    """An OIDC-only account must not be reachable by guessing an empty password."""
    assert not auth_service.verify_password("", None)
    assert not auth_service.verify_password("anything", None)


# --- registration ------------------------------------------------------------


def test_first_registration_becomes_admin_and_signs_in(anonymous_client: TestClient) -> None:
    response = register(anonymous_client, "Owner@Example.com ", "a-long-enough-password")

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "owner@example.com"  # normalized: trimmed and lowercased
    assert body["is_admin"] is True
    assert auth_service.SESSION_COOKIE_NAME in response.cookies

    assert anonymous_client.get("/api/v1/auth/me").json()["email"] == "owner@example.com"


def test_registration_closes_after_the_first_account(client: TestClient) -> None:
    response = register(client, OTHER["email"], OTHER["password"])

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "registration_closed"


def test_registration_can_be_reopened_and_the_second_user_is_not_admin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "allow_registration", True)

    response = register(client, OTHER["email"], OTHER["password"])

    assert response.status_code == 201
    assert response.json()["is_admin"] is False


def test_registration_rejects_a_short_password_and_a_non_address(
    anonymous_client: TestClient,
) -> None:
    assert register(anonymous_client, "owner@example.com", "short").status_code == 422
    assert register(anonymous_client, "not-an-email", "a-long-enough-password").status_code == 422
    # Neither attempt may leave a half-made account behind.
    assert anonymous_client.get("/api/v1/auth/config").json()["has_users"] is False


def test_registration_rejects_a_duplicate_email(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "allow_registration", True)

    assert register(client, TEST_EMAIL.upper(), "a-different-password").status_code == 422


# --- login and sessions ------------------------------------------------------


def test_login_is_case_insensitive_and_sets_a_session(anonymous_client: TestClient) -> None:
    register(anonymous_client, TEST_EMAIL, TEST_PASSWORD)
    anonymous_client.post("/api/v1/auth/logout")

    response = anonymous_client.post(
        "/api/v1/auth/login", json={"email": TEST_EMAIL.upper(), "password": TEST_PASSWORD}
    )

    assert response.status_code == 200
    assert anonymous_client.get("/api/v1/auth/me").status_code == 200


def test_login_reports_the_same_error_for_a_bad_password_and_an_unknown_email(
    client: TestClient,
) -> None:
    client.post("/api/v1/auth/logout")

    wrong_password = client.post(
        "/api/v1/auth/login", json={"email": TEST_EMAIL, "password": "not-the-password"}
    )
    unknown_email = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": TEST_PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"


def test_the_session_cookie_is_httponly_and_not_the_stored_token(client: TestClient) -> None:
    """A database copy must not hand out live sessions, so only the hash is stored."""
    token = client.cookies[auth_service.SESSION_COOKIE_NAME]
    login = client.post("/api/v1/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert "HttpOnly" in login.headers["set-cookie"]
    # Not Secure here: the default app_base_url is http, and a Secure cookie over plain http
    # would simply never come back, locking a local dev session out of its own app.
    assert "Secure" not in login.headers["set-cookie"]

    with Session(db.engine) as session:
        stored = session.exec(select(UserSession)).all()

    assert len(stored) == 2  # the fixture's registration, plus the login above
    assert token not in {record.id for record in stored}


def test_logout_ends_the_session_for_good(client: TestClient) -> None:
    token = client.cookies[auth_service.SESSION_COOKIE_NAME]

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401

    # Replaying the old cookie does not resurrect it.
    client.cookies.set(auth_service.SESSION_COOKIE_NAME, token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_an_expired_session_is_rejected_and_cleaned_up(client: TestClient) -> None:
    with Session(db.engine) as session:
        record = session.exec(select(UserSession)).one()
        record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.add(record)
        session.commit()

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"].lower()
    with Session(db.engine) as session:
        assert session.exec(select(UserSession)).all() == []


# --- what is and is not protected --------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/settings"),
        ("get", "/api/v1/queue"),
        ("get", "/api/v1/history"),
        ("get", "/api/v1/folders/tree"),
        ("post", "/api/v1/settings/ai/models"),
    ],
)
def test_every_data_route_needs_a_session(
    anonymous_client: TestClient, method: str, path: str
) -> None:
    response = getattr(anonymous_client, method)(path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_health_stays_public_because_the_container_healthcheck_uses_it(
    anonymous_client: TestClient,
) -> None:
    assert anonymous_client.get("/api/v1/health").status_code == 200


def test_auth_config_is_public_and_says_what_the_sign_in_screen_needs(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "oidc_issuer", "https://id.example.com")
    monkeypatch.setattr(config, "oidc_client_id", "router")
    monkeypatch.setattr(config, "oidc_client_secret", "s3cret")
    monkeypatch.setattr(config, "oidc_provider_name", "Authentik")

    before = anonymous_client.get("/api/v1/auth/config").json()
    assert before == {
        "oidc_enabled": True,
        "oidc_provider_name": "Authentik",
        "registration_open": True,
        "has_users": False,
    }

    register(anonymous_client, TEST_EMAIL, TEST_PASSWORD)
    after = anonymous_client.get("/api/v1/auth/config").json()
    assert after["has_users"] is True
    assert after["registration_open"] is False


def test_oidc_is_reported_disabled_without_a_client_secret(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A public client (id only) is deliberately not supported."""
    monkeypatch.setattr(config, "oidc_issuer", "https://id.example.com")
    monkeypatch.setattr(config, "oidc_client_id", "router")
    monkeypatch.setattr(config, "oidc_client_secret", "")

    assert anonymous_client.get("/api/v1/auth/config").json()["oidc_enabled"] is False


# --- OIDC --------------------------------------------------------------------

DISCOVERY = {
    "authorization_endpoint": "https://id.example.com/application/o/authorize/",
    "token_endpoint": "https://id.example.com/application/o/token/",
    "userinfo_endpoint": "https://id.example.com/application/o/userinfo/",
}


@pytest.fixture
def oidc_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "oidc_issuer", "https://id.example.com")
    monkeypatch.setattr(config, "oidc_client_id", "router")
    monkeypatch.setattr(config, "oidc_client_secret", "s3cret")
    monkeypatch.setattr(config, "app_base_url", "https://router.example.com")
    oidc_service._discovery_cache.clear()


def provider(
    *,
    token: dict[str, Any] | None = None,
    userinfo: dict[str, Any] | None = None,
    token_status: int = 200,
    seen: dict[str, Any] | None = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=DISCOVERY)
        if request.url.path.endswith("/token/"):
            if seen is not None:
                seen["token_form"] = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(token_status, json=token or {"access_token": "at"})
        if request.url.path.endswith("/userinfo/"):
            if seen is not None:
                seen["userinfo_auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json=userinfo or {"sub": "abc", "email": "sso@example.com"})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_begin_login_builds_a_pkce_request_and_records_the_flow(
    anonymous_client: TestClient, oidc_configured: None
) -> None:
    with Session(db.engine) as session, provider() as http:
        url = oidc_service.begin_login(session, http)
        pending = session.exec(select(OidcLogin)).all()

    params = httpx.QueryParams(url.split("?", 1)[1])
    assert url.startswith(DISCOVERY["authorization_endpoint"])
    assert params["client_id"] == "router"
    assert params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == "https://router.example.com/api/v1/auth/oidc/callback"
    # The verifier itself must never leave the server.
    assert len(pending) == 1
    assert pending[0].code_verifier not in url
    assert params["code_challenge"] != pending[0].code_verifier
    assert params["state"] == pending[0].state


def test_complete_login_exchanges_the_code_with_the_client_secret(
    anonymous_client: TestClient, oidc_configured: None
) -> None:
    seen: dict[str, Any] = {}
    with Session(db.engine) as session, provider(seen=seen) as http:
        url = oidc_service.begin_login(session, http)
        state = httpx.QueryParams(url.split("?", 1)[1])["state"]

        claims = oidc_service.complete_login(session, "the-code", state, http)

    assert claims == {"sub": "abc", "email": "sso@example.com"}
    assert seen["token_form"]["client_secret"] == "s3cret"
    assert seen["token_form"]["grant_type"] == "authorization_code"
    assert seen["token_form"]["code_verifier"]
    assert seen["userinfo_auth"] == "Bearer at"


def test_a_state_cannot_be_replayed(anonymous_client: TestClient, oidc_configured: None) -> None:
    with Session(db.engine) as session, provider() as http:
        url = oidc_service.begin_login(session, http)
        state = httpx.QueryParams(url.split("?", 1)[1])["state"]
        oidc_service.complete_login(session, "the-code", state, http)

        with pytest.raises(OidcError, match="expired or was already used"):
            oidc_service.complete_login(session, "the-code", state, http)


def test_complete_login_needs_an_email_claim(
    anonymous_client: TestClient, oidc_configured: None
) -> None:
    with Session(db.engine) as session, provider(userinfo={"sub": "abc"}) as http:
        url = oidc_service.begin_login(session, http)
        state = httpx.QueryParams(url.split("?", 1)[1])["state"]

        with pytest.raises(OidcError, match="email"):
            oidc_service.complete_login(session, "the-code", state, http)


def test_a_rejected_code_exchange_reports_the_status(
    anonymous_client: TestClient, oidc_configured: None
) -> None:
    with Session(db.engine) as session, provider(token_status=401) as http:
        url = oidc_service.begin_login(session, http)
        state = httpx.QueryParams(url.split("?", 1)[1])["state"]

        with pytest.raises(OidcError, match="401"):
            oidc_service.complete_login(session, "the-code", state, http)


def test_the_callback_creates_an_admin_on_a_fresh_instance(
    anonymous_client: TestClient, oidc_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        oidc_service,
        "complete_login",
        lambda *_args, **_kwargs: {"sub": "abc", "email": "SSO@example.com"},
    )

    response = anonymous_client.get(
        "/api/v1/auth/oidc/callback?code=c&state=s", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "https://router.example.com/"

    # Asserted on the header rather than through a follow-up request: app_base_url is https
    # here, so the cookie is Secure and a plain-http test client would (correctly) drop it.
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{auth_service.SESSION_COOKIE_NAME}=")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie

    with Session(db.engine) as session:
        users = session.exec(select(User)).all()
    assert len(users) == 1
    assert users[0].email == "sso@example.com"
    assert users[0].is_admin is True
    assert users[0].password_hash is None


def test_the_callback_links_onto_an_existing_password_account(
    client: TestClient, oidc_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signing in with SSO using the address of an existing account must not fork it."""
    monkeypatch.setattr(
        oidc_service,
        "complete_login",
        lambda *_args, **_kwargs: {"sub": "abc", "email": TEST_EMAIL},
    )
    client.post("/api/v1/auth/logout")

    client.get("/api/v1/auth/oidc/callback?code=c&state=s", follow_redirects=False)

    with Session(db.engine) as session:
        users = session.exec(select(User)).all()
    assert len(users) == 1
    assert users[0].oidc_subject == "abc"
    assert users[0].password_hash is not None  # the password still works too


def test_a_provider_error_lands_back_on_the_sign_in_screen(
    anonymous_client: TestClient, oidc_configured: None
) -> None:
    response = anonymous_client.get(
        "/api/v1/auth/oidc/callback?error=access_denied&error_description=Nope",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "https://router.example.com/login?error=Nope"


def test_oidc_login_is_refused_when_not_configured(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/api/v1/auth/oidc/login", follow_redirects=False)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "oidc_error"
