"""Accounts, passwords, and browser sessions.

Two ways in, one user table: a password or the configured OIDC provider (services/oidc.py).
Whoever registers first owns the instance and is made admin -- a fresh deployment is
reachable by anyone who knows the URL until someone claims it, so the window between "up"
and "claimed" is the thing worth keeping short, not the ceremony of an invite flow.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from app.config import settings as config
from app.models import User, UserSession
from app.services.errors import (
    AuthenticationRequired,
    InvalidCredentials,
    RegistrationClosed,
    ValidationError,
)

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"
MIN_PASSWORD_LENGTH = 12

# scrypt from the standard library rather than passlib/bcrypt: memory-hard, no new runtime
# dependency (CLAUDE.md rule 8). Parameters are the interactive-login set from RFC 7914 --
# ~16 MB and a few hundred milliseconds per attempt.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

# Deliberately permissive. This is an identity, not a deliverability check: the only thing
# worth rejecting is something that cannot be an address at all.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(derived).decode(),
        ]
    )


def verify_password(password: str, stored: str | None) -> bool:
    """False for an OIDC-only account: no password can ever match a row that has none."""
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(hash_b64)),
        )
    except (ValueError, TypeError):
        logger.warning("Stored password hash is unreadable")
        return False
    return hmac.compare_digest(derived, base64.b64decode(hash_b64))


def normalize_email(email: str) -> str:
    return email.strip().lower()


def user_count(session: Session) -> int:
    return int(session.exec(select(func.count()).select_from(User)).one())


def registration_open(session: Session) -> bool:
    """Always open while nobody has claimed the instance; after that, only if configured."""
    return user_count(session) == 0 or config.allow_registration


def register(session: Session, email: str, password: str) -> User:
    address = normalize_email(email)
    if not _EMAIL_RE.match(address):
        raise ValidationError("That doesn't look like an email address.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    is_first_user = user_count(session) == 0
    if not is_first_user and not config.allow_registration:
        raise RegistrationClosed(
            "This instance already has an account. Ask its admin to create one for you."
        )
    if _find_by_email(session, address) is not None:
        # Registration is not a place to leak "this address is taken" to a stranger... but
        # with registration closed after the first user, the only people who reach this are
        # the owner and their invitees, so a useful message beats a confusing one.
        raise ValidationError("An account with that email already exists.")

    user = User(
        email=address,
        password_hash=hash_password(password),
        is_admin=is_first_user,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    logger.info("Registered %s (admin=%s)", user.email, user.is_admin)
    return user


def authenticate(session: Session, email: str, password: str) -> User:
    user = _find_by_email(session, normalize_email(email))
    # Hash even when the account does not exist, so "no such user" and "wrong password" take
    # the same time and cannot be told apart by anyone probing for valid addresses.
    matches = verify_password(password, user.password_hash if user else None)
    if user is None or not matches:
        raise InvalidCredentials("Wrong email or password.")
    return user


def _find_by_email(session: Session, address: str) -> User | None:
    return session.exec(select(User).where(User.email == address)).first()


def start_session(session: Session, user: User) -> str:
    """Create a session row and return the raw token, which only the cookie ever holds."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session.add(
        UserSession(
            id=_token_hash(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=config.session_lifetime_days),
        )
    )
    user.last_login_at = now
    session.add(user)
    session.commit()
    return token


def resolve_session(session: Session, token: str | None) -> User:
    if not token:
        raise AuthenticationRequired("Sign in to continue.")

    record = session.get(UserSession, _token_hash(token))
    if record is None:
        raise AuthenticationRequired("Sign in to continue.")
    if _as_utc(record.expires_at) <= datetime.now(UTC):
        session.delete(record)
        session.commit()
        raise AuthenticationRequired("Your session has expired. Sign in again.")

    user = session.get(User, record.user_id)
    if user is None:
        session.delete(record)
        session.commit()
        raise AuthenticationRequired("Sign in to continue.")

    record.last_seen_at = datetime.now(UTC)
    session.add(record)
    session.commit()
    return user


def end_session(session: Session, token: str | None) -> None:
    """Idempotent: signing out twice, or with a stale cookie, is not an error."""
    if not token:
        return
    record = session.get(UserSession, _token_hash(token))
    if record is not None:
        session.delete(record)
        session.commit()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; compare them as the UTC they were stored as."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
