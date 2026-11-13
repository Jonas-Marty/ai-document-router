"""Endpoints and the task chains built from them (SPEC 6.3a, 8.7)."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter

from app.deps import AppSettingsDep, CurrentUserDep, SessionDep, WebDavDep
from app.models import AiEndpoint, AiTask
from app.schemas import (
    AiEndpointRead,
    AiEndpointWrite,
    AiModelsRequest,
    AiModelsResponse,
    AiTaskChainRead,
    AiTaskChainUpdate,
    AiTaskContextRead,
    AiTaskStepRead,
    AiUsageRead,
    AiUsageRowRead,
)
from app.services import ai_context, ai_tasks, ai_usage
from app.services.audit import Actor

router = APIRouter()


@router.get("/ai/endpoints")
def list_endpoints(session: SessionDep, user: CurrentUserDep) -> list[AiEndpointRead]:
    return [_read(session, endpoint) for endpoint in ai_tasks.list_endpoints(session, user.id)]


@router.post("/ai/endpoints")
def create_endpoint(
    payload: AiEndpointWrite, session: SessionDep, user: CurrentUserDep
) -> AiEndpointRead:
    endpoint = ai_tasks.create_endpoint(
        session,
        user.id,
        payload.name,
        payload.base_url,
        payload.api_key,
        timeout_seconds=payload.timeout_seconds,
        capabilities=_capabilities(payload),
        actor=Actor.from_user(user),
    )
    return _read(session, endpoint)


@router.put("/ai/endpoints/{endpoint_id}")
def update_endpoint(
    endpoint_id: str, payload: AiEndpointWrite, session: SessionDep, user: CurrentUserDep
) -> AiEndpointRead:
    endpoint = ai_tasks.update_endpoint(
        session,
        user.id,
        endpoint_id,
        payload.name,
        payload.base_url,
        payload.api_key,
        timeout_seconds=payload.timeout_seconds,
        capabilities=_capabilities(payload),
        actor=Actor.from_user(user),
    )
    return _read(session, endpoint)


def _capabilities(payload: AiEndpointWrite) -> ai_tasks.Capabilities:
    return ai_tasks.Capabilities(
        context_limit=payload.context_limit,
        json_mode=payload.json_mode,
        price_input_per_million=payload.price_input_per_million,
        price_output_per_million=payload.price_output_per_million,
    )


@router.delete("/ai/endpoints/{endpoint_id}")
def delete_endpoint(endpoint_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    ai_tasks.delete_endpoint(session, user.id, endpoint_id, actor=Actor.from_user(user))


@router.post("/ai/models")
def list_models(
    payload: AiModelsRequest, session: SessionDep, user: CurrentUserDep
) -> AiModelsResponse:
    models = ai_tasks.list_models(
        session, user.id, payload.base_url, payload.api_key, payload.endpoint_id
    )
    return AiModelsResponse(models=models)


@router.get("/ai/context")
def read_context(
    session: SessionDep, webdav: WebDavDep, app_settings: AppSettingsDep
) -> list[AiTaskContextRead]:
    """What each task is sent right now. Walks WebDAV for the folder tree, so fetched on demand."""
    return [
        AiTaskContextRead(
            task=context.task,
            chain=context.chain,
            system_prompt=context.system_prompt,
            user_prompt=context.user_prompt,
            note=context.note,
        )
        for context in ai_context.preview(session, webdav, app_settings)
    ]


@router.get("/ai/tasks")
def read_chains(session: SessionDep, user: CurrentUserDep) -> list[AiTaskChainRead]:
    return [_chain(session, user.id, task) for task in AiTask]


@router.put("/ai/tasks/{task}")
def write_chain(
    task: AiTask, payload: AiTaskChainUpdate, session: SessionDep, user: CurrentUserDep
) -> AiTaskChainRead:
    ai_tasks.replace_chain(
        session,
        user.id,
        task,
        [(step.endpoint_id, step.model_name) for step in payload.steps],
        actor=Actor.from_user(user),
    )
    return _chain(session, user.id, task)


@router.get("/ai/usage")
def read_usage(session: SessionDep, user: CurrentUserDep, start: date, end: date) -> AiUsageRead:
    """Tokens per day, connection, model and feature from `start` to `end`, inclusive."""
    report = ai_usage.report(session, user.id, start, end)
    return AiUsageRead(
        rows=[AiUsageRowRead(**asdict(row)) for row in report.rows],
        analyses=report.analyses,
        comparisons=report.comparisons,
    )


def _read(session: SessionDep, endpoint: AiEndpoint) -> AiEndpointRead:
    return AiEndpointRead(
        id=endpoint.id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        api_key_set=endpoint.api_key_encrypted is not None,
        timeout_seconds=endpoint.timeout_seconds,
        context_limit=endpoint.context_limit,
        json_mode=endpoint.json_mode,
        price_input_per_million=endpoint.price_input_per_million,
        price_output_per_million=endpoint.price_output_per_million,
        used_by=ai_tasks.endpoint_in_use(session, endpoint.id),
    )


def _chain(session: SessionDep, user_id: str, task: AiTask) -> AiTaskChainRead:
    return AiTaskChainRead(
        task=task,
        steps=[
            AiTaskStepRead(
                endpoint_id=step.endpoint_id,
                endpoint_name=step.endpoint_name,
                model_name=step.model_name,
            )
            for step in ai_tasks.chain_for(session, user_id, task)
        ],
    )
