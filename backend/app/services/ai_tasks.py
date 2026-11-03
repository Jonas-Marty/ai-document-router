"""Endpoints, the chains that assign them to tasks, and running a call down a chain.

SPEC 6.3a. A task is configured as an ordered list of (endpoint, model) steps, and a call
walks that list until one answers. That is the whole point of the feature: the machine at
home is first because it is free and private, and a hosted provider sits behind it so a
document still gets read on the days that machine is off.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlmodel import Session, select

from app.config import settings as config
from app.models import AiEndpoint, AiTask, AiTaskStep, AuditAction
from app.services import ai, audit, crypto, metrics, progress, urls
from app.services.ai import AIUnavailable
from app.services.audit import Actor
from app.services.errors import NotFoundError, ValidationError
from app.services.times import utc_now

logger = logging.getLogger(__name__)

_VERBS = {AiTask.extraction: "Reading the document", AiTask.filing: "Choosing a name and folder"}


@dataclass(frozen=True)
class ResolvedStep:
    """A step with its endpoint's connection details already decrypted, ready to call."""

    endpoint_name: str
    endpoint_url: str
    api_key: str | None
    model_name: str
    endpoint_id: str | None = None
    position: int = 0
    timeout_seconds: float = ai.REQUEST_TIMEOUT_SECONDS

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
            fallback=self.position > 0,
        )


@dataclass(frozen=True)
class ChainStep:
    """A step as the settings screen shows it. No key: nothing here is going to call anything."""

    endpoint_id: str
    endpoint_name: str
    model_name: str


class NoStepsConfigured(AIUnavailable):
    """The task has no endpoints assigned, so there was nothing to try."""


def chain_for(session: Session, user_id: str, task: AiTask) -> list[ChainStep]:
    return [
        ChainStep(endpoint_id=endpoint.id, endpoint_name=endpoint.name, model_name=step.model_name)
        for step, endpoint in _rows(session, user_id, task)
    ]


def resolve_chain(session: Session, user_id: str, task: AiTask) -> list[ResolvedStep]:
    """Every step of one user's chain for a task, in the order they should be tried."""
    return [
        ResolvedStep(
            endpoint_name=endpoint.name,
            endpoint_url=endpoint.base_url,
            api_key=decrypt_key(endpoint),
            model_name=step.model_name,
            endpoint_id=endpoint.id,
            position=position,
            timeout_seconds=endpoint.timeout_seconds,
        )
        for position, (step, endpoint) in enumerate(_rows(session, user_id, task))
    ]


def _rows(session: Session, user_id: str, task: AiTask) -> list[tuple[AiTaskStep, AiEndpoint]]:
    return list(
        session.exec(
            select(AiTaskStep, AiEndpoint)
            .join(AiEndpoint, AiEndpoint.id == AiTaskStep.endpoint_id)  # type: ignore[arg-type]
            .where(AiTaskStep.user_id == user_id, AiTaskStep.task == task)
            .order_by(AiTaskStep.position)  # type: ignore[arg-type]
        ).all()
    )


def run_chain[T](steps: list[ResolvedStep], call: Callable[[ResolvedStep], T], task: AiTask) -> T:
    """Try each step in turn, returning the first result.

    Only AIUnavailable moves to the next step -- that is an endpoint problem, which is exactly
    what the next endpoint is for. Anything else (a reply that failed validation, say) is the
    model having answered, and re-asking a different one would hide a problem the person needs
    to see while spending a second call to do it.
    """
    if not steps:
        raise NoStepsConfigured(
            f"No AI endpoint is assigned to the {task} task yet — set one in Settings."
        )

    failures: list[str] = []
    unanswered: list[str] = []
    for position, step in enumerate(steps):
        progress.report(
            f"{_VERBS.get(task, str(task))} with {step.label}…"
            + (f" ({', '.join(unanswered)} didn't answer)" if unanswered else "")
        )
        started = time.monotonic()
        try:
            result = call(step)
        except AIUnavailable as exc:
            _observe_step(task, step, "unavailable", started)
            if position < len(steps) - 1:
                metrics.ai_fallbacks.labels(task).inc()
            logger.warning(
                "%s via %s unavailable after %.1fs: %s",
                task,
                step.label,
                time.monotonic() - started,
                exc.message,
            )
            failures.append(f"{step.label} ({exc.message})")
            unanswered.append(step.label)
            continue
        except Exception as exc:
            _observe_step(task, step, "rejected", started)
            # Not a fallback case (see above), but still the line that says which model
            # produced the unusable reply -- the exception itself only says what was wrong.
            logger.info(
                "%s via %s answered in %.1fs, unusably: %s",
                task,
                step.label,
                time.monotonic() - started,
                getattr(exc, "reason", None) or getattr(exc, "message", None) or exc,
            )
            raise
        _observe_step(task, step, "ok", started)
        logger.info("%s via %s answered in %.1fs", task, step.label, time.monotonic() - started)
        return result

    metrics.ai_chain_exhausted.labels(task).inc()
    raise AIUnavailable(f"Every {task} endpoint failed. Tried: {'; '.join(failures)}")


def _observe_step(task: AiTask, step: ResolvedStep, outcome: str, started: float) -> None:
    metrics.ai_requests.labels(task, step.label, outcome).inc()
    metrics.ai_request_duration.labels(task, step.label).observe(time.monotonic() - started)


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


def endpoint_in_use(session: Session, endpoint_id: str) -> list[AiTask]:
    """Which tasks would lose a step if this endpoint went away."""
    steps = session.exec(select(AiTaskStep).where(AiTaskStep.endpoint_id == endpoint_id)).all()
    return sorted({step.task for step in steps})


def replace_chain(
    session: Session,
    user_id: str,
    task: AiTask,
    steps: list[tuple[str, str]],
    *,
    actor: Actor,
    commit: bool = True,
) -> None:
    """Set a task's chain to exactly these (endpoint_id, model_name) pairs, in order.

    Replaced wholesale rather than diffed: the chain is an ordered list the user edits as a
    whole, and reconciling it row by row would be work in service of nothing.
    """
    new_labels = []
    for endpoint_id, model_name in steps:
        endpoint = get_endpoint(session, user_id, endpoint_id)
        if not model_name.strip():
            raise ValidationError("Every step needs a model name.")
        new_labels.append(f"{endpoint.name} · {model_name.strip()}")
    old_labels = [
        f"{step.endpoint_name} · {step.model_name}" for step in chain_for(session, user_id, task)
    ]

    for existing in session.exec(
        select(AiTaskStep).where(AiTaskStep.user_id == user_id, AiTaskStep.task == task)
    ).all():
        session.delete(existing)
    session.flush()

    for position, (endpoint_id, model_name) in enumerate(steps):
        session.add(
            AiTaskStep(
                user_id=user_id,
                task=task,
                position=position,
                endpoint_id=endpoint_id,
                model_name=model_name.strip(),
            )
        )
    audit.record(
        session,
        AuditAction.ai_task_updated,
        actor=actor,
        summary=(
            f"Set the {task} chain to {' → '.join(new_labels)}"
            if new_labels
            else f"Cleared the {task} chain"
        ),
        details={"task": str(task), "from": old_labels, "to": new_labels},
    )
    if commit:
        session.commit()


def create_endpoint(
    session: Session,
    user_id: str,
    name: str,
    base_url: str,
    api_key: str | None,
    *,
    timeout_seconds: int | None = None,
    actor: Actor,
) -> AiEndpoint:
    endpoint = AiEndpoint(
        user_id=user_id,
        name=_validate_name(session, user_id, name, endpoint_id=None),
        base_url=validate_endpoint_url(base_url),
        api_key_encrypted=crypto.encrypt(config.secret_key, api_key) if api_key else None,
        timeout_seconds=_validate_timeout(timeout_seconds),
        created_at=utc_now(),
    )
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
    actor: Actor,
) -> AiEndpoint:
    """A blank key leaves the stored one alone -- the form is never given it back to resend."""
    endpoint = get_endpoint(session, user_id, endpoint_id)
    before: dict[str, object] = {
        "name": endpoint.name,
        "base_url": endpoint.base_url,
        "timeout_seconds": endpoint.timeout_seconds,
    }
    endpoint.name = _validate_name(session, user_id, name, endpoint_id=endpoint_id)
    endpoint.base_url = validate_endpoint_url(base_url)
    if timeout_seconds is not None:
        endpoint.timeout_seconds = _validate_timeout(timeout_seconds)
    if api_key:
        endpoint.api_key_encrypted = crypto.encrypt(config.secret_key, api_key)

    session.add(endpoint)
    after: dict[str, object] = {
        "name": endpoint.name,
        "base_url": endpoint.base_url,
        "timeout_seconds": endpoint.timeout_seconds,
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
    """Refused while a task still points at it, rather than quietly shortening that chain."""
    endpoint = get_endpoint(session, user_id, endpoint_id)
    in_use = endpoint_in_use(session, endpoint_id)
    if in_use:
        tasks = " and ".join(str(task) for task in in_use)
        raise ValidationError(
            f"'{endpoint.name}' is still assigned to the {tasks} task. Remove it there first."
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
