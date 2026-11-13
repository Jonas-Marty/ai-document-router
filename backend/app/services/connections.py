"""Connections (AI endpoints): storing them, and calling one model on one of them.

SPEC 6.3a. A connection is an OpenAI-compatible server the person named. Which model on
which connection does what is a configuration's business (services/configurations.py);
this module only knows how to reach one, and never tries another on its own.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlmodel import Session, select

from app.config import settings as config
from app.models import AiConfiguration, AiEndpoint, AiJsonMode, AiTask, AuditAction
from app.services import ai, audit, crypto, metrics, progress, urls
from app.services.ai import AIUnavailable, PromptTooLong, Unreachable
from app.services.audit import Actor
from app.services.errors import NotFoundError, ValidationError
from app.services.times import utc_now

logger = logging.getLogger(__name__)

_VERBS = {AiTask.extraction: "Reading the document", AiTask.filing: "Choosing a name and folder"}


@dataclass(frozen=True)
class ResolvedStep:
    """A connection and model with the key already decrypted, ready to call."""

    endpoint_name: str
    endpoint_url: str
    api_key: str | None
    model_name: str
    endpoint_id: str | None = None
    timeout_seconds: float = ai.REQUEST_TIMEOUT_SECONDS
    context_limit: int | None = None
    json_mode: AiJsonMode = AiJsonMode.auto
    price_input: float | None = None
    price_output: float | None = None

    @classmethod
    def of(cls, endpoint: AiEndpoint, model_name: str) -> "ResolvedStep":
        return cls(
            endpoint_name=endpoint.name,
            endpoint_url=endpoint.base_url,
            api_key=decrypt_key(endpoint),
            model_name=model_name,
            endpoint_id=endpoint.id,
            timeout_seconds=endpoint.timeout_seconds,
            context_limit=endpoint.context_limit,
            json_mode=endpoint.json_mode,
            price_input=endpoint.price_input_per_million,
            price_output=endpoint.price_output_per_million,
        )

    @property
    def label(self) -> str:
        """How this step is named in a failure message, and on the review screen."""
        return f"{self.endpoint_name} · {self.model_name}"

    def usage(self, meter: ai.UsageMeter, task: AiTask) -> ai.UsageRecorder:
        """A recorder for calls made through this step, carrying what usage is grouped by."""
        return meter.scope(
            task,
            self.label,
            endpoint_id=self.endpoint_id,
            connection=self.endpoint_name,
            model=self.model_name,
            price_input=self.price_input,
            price_output=self.price_output,
        )


def call_step[T](step: ResolvedStep, call: Callable[[ResolvedStep], T], task: AiTask) -> T:
    """Make one step's call, saying so on the review screen, in the log and in /metrics.

    Exceptions pass through unchanged: what a failure means for the document (wait, or fail
    with the reason) is the caller's decision, never a reason to try somewhere else.
    """
    progress.report(f"{_VERBS.get(task, str(task))} with {step.label}…")
    started = time.monotonic()
    try:
        result = call(step)
    except PromptTooLong as exc:
        _observe(task, step, "too_long", started)
        logger.warning("%s via %s not sent: %s", task, step.label, exc.message)
        raise
    except Unreachable as exc:
        _observe(task, step, "unreachable", started)
        logger.warning("%s via %s unreachable: %s", task, step.label, exc.message)
        raise
    except AIUnavailable as exc:
        _observe(task, step, "unavailable", started)
        logger.warning(
            "%s via %s failed after %.1fs: %s",
            task,
            step.label,
            time.monotonic() - started,
            exc.message,
        )
        raise
    except Exception as exc:
        _observe(task, step, "rejected", started)
        # The line that says which model produced the unusable reply -- the exception itself
        # only says what was wrong with it.
        logger.info(
            "%s via %s answered in %.1fs, unusably: %s",
            task,
            step.label,
            time.monotonic() - started,
            getattr(exc, "reason", None) or getattr(exc, "message", None) or exc,
        )
        raise
    _observe(task, step, "ok", started)
    logger.info("%s via %s answered in %.1fs", task, step.label, time.monotonic() - started)
    return result


def _observe(task: AiTask, step: ResolvedStep, outcome: str, started: float) -> None:
    metrics.ai_requests.labels(task, step.label, outcome).inc()
    metrics.ai_request_duration.labels(task, step.label).observe(time.monotonic() - started)


def is_reachable(step: ResolvedStep) -> bool:
    """Whether the connection answers at all (SPEC 6.3a). Any HTTP reply counts: a wrong key
    is a reason for the call to fail with that reason, not for the document to wait."""
    return ai.probe(endpoint_url=step.endpoint_url, api_key=step.api_key)


def decrypt_key(endpoint: AiEndpoint) -> str | None:
    if endpoint.api_key_encrypted is None:
        return None
    return crypto.decrypt(config.secret_key, endpoint.api_key_encrypted)


def get_endpoint(session: Session, user_id: str, endpoint_id: str) -> AiEndpoint:
    """Someone else's endpoint is as missing as a deleted one: 404, not 403, so ids do not
    confirm that an endpoint exists."""
    endpoint = session.get(AiEndpoint, endpoint_id)
    if endpoint is None or endpoint.user_id != user_id:
        raise NotFoundError("That AI endpoint no longer exists.")
    return endpoint


def list_endpoints(session: Session, user_id: str) -> list[AiEndpoint]:
    return list(
        session.exec(
            select(AiEndpoint).where(AiEndpoint.user_id == user_id).order_by(AiEndpoint.name)
        ).all()
    )


def endpoint_in_use(session: Session, endpoint_id: str) -> list[str]:
    """Names of the configurations that use this endpoint for either step."""
    rows = session.exec(
        select(AiConfiguration.name).where(
            (AiConfiguration.reading_endpoint_id == endpoint_id)
            | (AiConfiguration.filing_endpoint_id == endpoint_id)
        )
    ).all()
    return sorted(rows)


def create_endpoint(
    session: Session,
    user_id: str,
    name: str,
    base_url: str,
    api_key: str | None,
    *,
    timeout_seconds: int | None = None,
    capabilities: "Capabilities | None" = None,
    actor: Actor,
) -> AiEndpoint:
    caps = (capabilities or Capabilities()).validated()
    endpoint = AiEndpoint(
        user_id=user_id,
        name=_validate_name(session, user_id, name, endpoint_id=None),
        base_url=validate_endpoint_url(base_url),
        api_key_encrypted=crypto.encrypt(config.secret_key, api_key) if api_key else None,
        timeout_seconds=_validate_timeout(timeout_seconds),
        created_at=utc_now(),
    )
    caps.apply(endpoint)
    session.add(endpoint)
    audit.record(
        session,
        AuditAction.ai_endpoint_created,
        actor=actor,
        summary=f"Added the AI endpoint '{endpoint.name}'",
        # Whether a key was given, never the key: CLAUDE.md rule 5 covers this table too.
        details={
            "name": endpoint.name,
            "base_url": endpoint.base_url,
            "timeout_seconds": endpoint.timeout_seconds,
            **Capabilities.of(endpoint).describe(),
            "api_key_set": endpoint.api_key_encrypted is not None,
        },
    )
    session.commit()
    session.refresh(endpoint)
    return endpoint


def update_endpoint(
    session: Session,
    user_id: str,
    endpoint_id: str,
    name: str,
    base_url: str,
    api_key: str | None,
    *,
    timeout_seconds: int | None = None,
    capabilities: "Capabilities | None" = None,
    actor: Actor,
) -> AiEndpoint:
    """A blank key leaves the stored one alone -- the form is never given it back to resend.
    Capabilities are replaced as given: the form always sends all of them."""
    endpoint = get_endpoint(session, user_id, endpoint_id)
    caps = (capabilities or Capabilities()).validated()
    before: dict[str, object] = {
        "name": endpoint.name,
        "base_url": endpoint.base_url,
        "timeout_seconds": endpoint.timeout_seconds,
        **Capabilities.of(endpoint).describe(),
    }
    endpoint.name = _validate_name(session, user_id, name, endpoint_id=endpoint_id)
    endpoint.base_url = validate_endpoint_url(base_url)
    if timeout_seconds is not None:
        endpoint.timeout_seconds = _validate_timeout(timeout_seconds)
    if api_key:
        endpoint.api_key_encrypted = crypto.encrypt(config.secret_key, api_key)
    caps.apply(endpoint)

    session.add(endpoint)
    after: dict[str, object] = {
        "name": endpoint.name,
        "base_url": endpoint.base_url,
        "timeout_seconds": endpoint.timeout_seconds,
        **Capabilities.of(endpoint).describe(),
    }
    audit.record(
        session,
        AuditAction.ai_endpoint_updated,
        actor=actor,
        summary=f"Edited the AI endpoint '{endpoint.name}'",
        details={
            "changed": {
                field: {"from": before[field], "to": after[field]}
                for field in before
                if before[field] != after[field]
            },
            # That it changed, and nothing else about it (rule 5).
            "api_key_changed": bool(api_key),
        },
    )
    session.commit()
    session.refresh(endpoint)
    return endpoint


MIN_CONTEXT_LIMIT = 1024
MAX_CONTEXT_LIMIT = 2_000_000
MAX_PRICE_PER_MILLION = 10_000.0


@dataclass(frozen=True)
class Capabilities:
    """What a connection's server can take and what it charges (SPEC 6.3b). Not secrets."""

    context_limit: int | None = None
    json_mode: AiJsonMode = AiJsonMode.auto
    price_input_per_million: float | None = None
    price_output_per_million: float | None = None

    @classmethod
    def of(cls, endpoint: AiEndpoint) -> "Capabilities":
        return cls(
            context_limit=endpoint.context_limit,
            json_mode=endpoint.json_mode,
            price_input_per_million=endpoint.price_input_per_million,
            price_output_per_million=endpoint.price_output_per_million,
        )

    def validated(self) -> "Capabilities":
        limit = self.context_limit
        if limit is not None and not MIN_CONTEXT_LIMIT <= limit <= MAX_CONTEXT_LIMIT:
            raise ValidationError(
                f"The context limit must be between {MIN_CONTEXT_LIMIT:,} and "
                f"{MAX_CONTEXT_LIMIT:,} tokens, or left empty."
            )
        for price in (self.price_input_per_million, self.price_output_per_million):
            if price is not None and not 0 <= price <= MAX_PRICE_PER_MILLION:
                raise ValidationError(
                    f"A price per million tokens must be between 0 and "
                    f"{MAX_PRICE_PER_MILLION:,.0f}, or left empty."
                )
        return self

    def apply(self, endpoint: AiEndpoint) -> None:
        endpoint.context_limit = self.context_limit
        endpoint.json_mode = self.json_mode
        endpoint.price_input_per_million = self.price_input_per_million
        endpoint.price_output_per_million = self.price_output_per_million

    def describe(self) -> dict[str, object]:
        """For audit details; plain values so the JSON column takes them."""
        return {
            "context_limit": self.context_limit,
            "json_mode": str(self.json_mode),
            "price_input_per_million": self.price_input_per_million,
            "price_output_per_million": self.price_output_per_million,
        }


MIN_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 900
DEFAULT_TIMEOUT_SECONDS = 120


def _validate_timeout(value: int | None) -> int:
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    if not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        raise ValidationError(
            f"The reply timeout must be between {MIN_TIMEOUT_SECONDS} and "
            f"{MAX_TIMEOUT_SECONDS} seconds."
        )
    return value


def delete_endpoint(session: Session, user_id: str, endpoint_id: str, *, actor: Actor) -> None:
    """Refused while a configuration still uses it, rather than leaving that one broken."""
    endpoint = get_endpoint(session, user_id, endpoint_id)
    in_use = endpoint_in_use(session, endpoint_id)
    if in_use:
        names = ", ".join(f"'{name}'" for name in in_use)
        raise ValidationError(
            f"'{endpoint.name}' is still used by the configuration {names}. "
            "Change or delete that first."
        )

    session.delete(endpoint)
    audit.record(
        session,
        AuditAction.ai_endpoint_deleted,
        actor=actor,
        summary=f"Removed the AI endpoint '{endpoint.name}'",
        details={"name": endpoint.name, "base_url": endpoint.base_url},
    )
    session.commit()


def list_models(
    session: Session, user_id: str, base_url: str, api_key: str | None, endpoint_id: str | None
) -> list[str]:
    """Models offered by an endpoint, saved or still being typed into the add form.

    The URL comes from the request rather than the database so the button works before the
    endpoint is saved -- which is the point, since it is how you find out the URL is wrong.
    """
    url = validate_endpoint_url(base_url)
    key = (api_key or "").strip()
    if not key and endpoint_id is not None:
        key = decrypt_key(get_endpoint(session, user_id, endpoint_id)) or ""

    return ai.list_models(endpoint_url=url, api_key=key or None)


def validate_endpoint_url(url: str) -> str:
    """https anywhere, http only on a private host: the key travels on this connection."""
    return urls.validate_service_url(url, "Endpoint URL")


def _validate_name(session: Session, user_id: str, name: str, *, endpoint_id: str | None) -> str:
    trimmed = name.strip()
    if not trimmed:
        raise ValidationError("Give the endpoint a name so you can tell it apart from the others.")

    clash = session.exec(
        select(AiEndpoint).where(AiEndpoint.user_id == user_id, AiEndpoint.name == trimmed)
    ).first()
    if clash is not None and clash.id != endpoint_id:
        raise ValidationError(f"There is already an endpoint called '{trimmed}'.")
    return trimmed
