"""Endpoints, the chains that assign them to tasks, and running a call down a chain.

SPEC 6.3a. A task is configured as an ordered list of (endpoint, model) steps, and a call
walks that list until one answers. That is the whole point of the feature: the machine at
home is first because it is free and private, and a hosted provider sits behind it so a
document still gets read on the days that machine is off.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from sqlmodel import Session, select

from app.config import settings as config
from app.models import AiEndpoint, AiTask, AiTaskStep
from app.services import ai, crypto
from app.services.ai import AIUnavailable
from app.services.errors import NotFoundError, ValidationError
from app.services.times import utc_now

logger = logging.getLogger(__name__)

_PRIVATE_NETWORKS = [
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),
]


@dataclass(frozen=True)
class ResolvedStep:
    """A step with its endpoint's connection details already decrypted, ready to call."""

    endpoint_name: str
    endpoint_url: str
    api_key: str | None
    model_name: str

    @property
    def label(self) -> str:
        """How this step is named in a failure message, and on the review screen."""
        return f"{self.endpoint_name} · {self.model_name}"


@dataclass(frozen=True)
class ChainStep:
    """A step as the settings screen shows it. No key: nothing here is going to call anything."""

    endpoint_id: str
    endpoint_name: str
    model_name: str


class NoStepsConfigured(AIUnavailable):
    """The task has no endpoints assigned, so there was nothing to try."""


def chain_for(session: Session, task: AiTask) -> list[ChainStep]:
    return [
        ChainStep(endpoint_id=endpoint.id, endpoint_name=endpoint.name, model_name=step.model_name)
        for step, endpoint in _rows(session, task)
    ]


def resolve_chain(session: Session, task: AiTask) -> list[ResolvedStep]:
    """Every step for a task, in the order they should be tried."""
    return [
        ResolvedStep(
            endpoint_name=endpoint.name,
            endpoint_url=endpoint.base_url,
            api_key=decrypt_key(endpoint),
            model_name=step.model_name,
        )
        for step, endpoint in _rows(session, task)
    ]


def _rows(session: Session, task: AiTask) -> list[tuple[AiTaskStep, AiEndpoint]]:
    return list(
        session.exec(
            select(AiTaskStep, AiEndpoint)
            .join(AiEndpoint, AiEndpoint.id == AiTaskStep.endpoint_id)  # type: ignore[arg-type]
            .where(AiTaskStep.task == task)
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
    for step in steps:
        try:
            return call(step)
        except AIUnavailable as exc:
            logger.warning("%s step %s unavailable: %s", task, step.label, exc.message)
            failures.append(f"{step.label} ({exc.message})")

    raise AIUnavailable(f"Every {task} endpoint failed. Tried: {'; '.join(failures)}")


def decrypt_key(endpoint: AiEndpoint) -> str | None:
    if endpoint.api_key_encrypted is None:
        return None
    return crypto.decrypt(config.secret_key, endpoint.api_key_encrypted)


def get_endpoint(session: Session, endpoint_id: str) -> AiEndpoint:
    endpoint = session.get(AiEndpoint, endpoint_id)
    if endpoint is None:
        raise NotFoundError("That AI endpoint no longer exists.")
    return endpoint


def list_endpoints(session: Session) -> list[AiEndpoint]:
    return list(session.exec(select(AiEndpoint).order_by(AiEndpoint.name)).all())


def endpoint_in_use(session: Session, endpoint_id: str) -> list[AiTask]:
    """Which tasks would lose a step if this endpoint went away."""
    steps = session.exec(select(AiTaskStep).where(AiTaskStep.endpoint_id == endpoint_id)).all()
    return sorted({step.task for step in steps})


def replace_chain(
    session: Session, task: AiTask, steps: list[tuple[str, str]], *, commit: bool = True
) -> None:
    """Set a task's chain to exactly these (endpoint_id, model_name) pairs, in order.

    Replaced wholesale rather than diffed: the chain is an ordered list the user edits as a
    whole, and reconciling it row by row would be work in service of nothing.
    """
    for endpoint_id, model_name in steps:
        get_endpoint(session, endpoint_id)
        if not model_name.strip():
            raise ValidationError("Every step needs a model name.")

    for existing in session.exec(select(AiTaskStep).where(AiTaskStep.task == task)).all():
        session.delete(existing)
    session.flush()

    for position, (endpoint_id, model_name) in enumerate(steps):
        session.add(
            AiTaskStep(
                task=task,
                position=position,
                endpoint_id=endpoint_id,
                model_name=model_name.strip(),
            )
        )
    if commit:
        session.commit()


def create_endpoint(session: Session, name: str, base_url: str, api_key: str | None) -> AiEndpoint:
    endpoint = AiEndpoint(
        name=_validate_name(session, name, endpoint_id=None),
        base_url=validate_endpoint_url(base_url),
        api_key_encrypted=crypto.encrypt(config.secret_key, api_key) if api_key else None,
        created_at=utc_now(),
    )
    session.add(endpoint)
    session.commit()
    session.refresh(endpoint)
    return endpoint


def update_endpoint(
    session: Session, endpoint_id: str, name: str, base_url: str, api_key: str | None
) -> AiEndpoint:
    """A blank key leaves the stored one alone -- the form is never given it back to resend."""
    endpoint = get_endpoint(session, endpoint_id)
    endpoint.name = _validate_name(session, name, endpoint_id=endpoint_id)
    endpoint.base_url = validate_endpoint_url(base_url)
    if api_key:
        endpoint.api_key_encrypted = crypto.encrypt(config.secret_key, api_key)

    session.add(endpoint)
    session.commit()
    session.refresh(endpoint)
    return endpoint


def delete_endpoint(session: Session, endpoint_id: str) -> None:
    """Refused while a task still points at it, rather than quietly shortening that chain."""
    endpoint = get_endpoint(session, endpoint_id)
    in_use = endpoint_in_use(session, endpoint_id)
    if in_use:
        tasks = " and ".join(str(task) for task in in_use)
        raise ValidationError(
            f"'{endpoint.name}' is still assigned to the {tasks} task. Remove it there first."
        )

    session.delete(endpoint)
    session.commit()


def list_models(
    session: Session, base_url: str, api_key: str | None, endpoint_id: str | None
) -> list[str]:
    """Models offered by an endpoint, saved or still being typed into the add form.

    The URL comes from the request rather than the database so the button works before the
    endpoint is saved -- which is the point, since it is how you find out the URL is wrong.
    """
    url = validate_endpoint_url(base_url)
    key = (api_key or "").strip()
    if not key and endpoint_id is not None:
        key = decrypt_key(get_endpoint(session, endpoint_id)) or ""

    return ai.list_models(endpoint_url=url, api_key=key or None)


def validate_endpoint_url(url: str) -> str:
    """https anywhere, http only on a private host: the key travels on this connection."""
    trimmed = url.strip()
    if not trimmed:
        raise ValidationError("An endpoint needs a URL.")

    parsed = urlparse(trimmed)
    if parsed.scheme == "https" and parsed.hostname:
        return trimmed
    if parsed.scheme == "http" and _is_private_host(parsed.hostname):
        return trimmed
    raise ValidationError("Endpoint URL must use https://, or http:// for a private network host.")


def _validate_name(session: Session, name: str, *, endpoint_id: str | None) -> str:
    trimmed = name.strip()
    if not trimmed:
        raise ValidationError("Give the endpoint a name so you can tell it apart from the others.")

    clash = session.exec(select(AiEndpoint).where(AiEndpoint.name == trimmed)).first()
    if clash is not None and clash.id != endpoint_id:
        raise ValidationError(f"There is already an endpoint called '{trimmed}'.")
    return trimmed


def _is_private_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname == "localhost":
        return True
    try:
        addr = ip_address(hostname)
    except ValueError:
        return False
    return any(addr in network for network in _PRIVATE_NETWORKS)
