"""Endpoints and the task chains built from them (SPEC 6.3a, 8.7)."""

from fastapi import APIRouter

from app.deps import CurrentUserDep, SessionDep
from app.models import AiEndpoint, AiTask
from app.schemas import (
    AiEndpointRead,
    AiEndpointWrite,
    AiModelsRequest,
    AiModelsResponse,
    AiTaskChainRead,
    AiTaskChainUpdate,
    AiTaskStepRead,
)
from app.services import ai_tasks

router = APIRouter()


@router.get("/ai/endpoints")
def list_endpoints(session: SessionDep, _user: CurrentUserDep) -> list[AiEndpointRead]:
    return [_read(session, endpoint) for endpoint in ai_tasks.list_endpoints(session)]


@router.post("/ai/endpoints")
def create_endpoint(
    payload: AiEndpointWrite, session: SessionDep, _user: CurrentUserDep
) -> AiEndpointRead:
    endpoint = ai_tasks.create_endpoint(session, payload.name, payload.base_url, payload.api_key)
    return _read(session, endpoint)


@router.put("/ai/endpoints/{endpoint_id}")
def update_endpoint(
    endpoint_id: str, payload: AiEndpointWrite, session: SessionDep, _user: CurrentUserDep
) -> AiEndpointRead:
    endpoint = ai_tasks.update_endpoint(
        session, endpoint_id, payload.name, payload.base_url, payload.api_key
    )
    return _read(session, endpoint)


@router.delete("/ai/endpoints/{endpoint_id}")
def delete_endpoint(endpoint_id: str, session: SessionDep, _user: CurrentUserDep) -> None:
    ai_tasks.delete_endpoint(session, endpoint_id)


@router.post("/ai/models")
def list_models(
    payload: AiModelsRequest, session: SessionDep, _user: CurrentUserDep
) -> AiModelsResponse:
    models = ai_tasks.list_models(session, payload.base_url, payload.api_key, payload.endpoint_id)
    return AiModelsResponse(models=models)


@router.get("/ai/tasks")
def read_chains(session: SessionDep, _user: CurrentUserDep) -> list[AiTaskChainRead]:
    return [_chain(session, task) for task in AiTask]


@router.put("/ai/tasks/{task}")
def write_chain(
    task: AiTask, payload: AiTaskChainUpdate, session: SessionDep, _user: CurrentUserDep
) -> AiTaskChainRead:
    ai_tasks.replace_chain(
        session, task, [(step.endpoint_id, step.model_name) for step in payload.steps]
    )
    return _chain(session, task)


def _read(session: SessionDep, endpoint: AiEndpoint) -> AiEndpointRead:
    return AiEndpointRead(
        id=endpoint.id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        api_key_set=endpoint.api_key_encrypted is not None,
        used_by=ai_tasks.endpoint_in_use(session, endpoint.id),
    )


def _chain(session: SessionDep, task: AiTask) -> AiTaskChainRead:
    return AiTaskChainRead(
        task=task,
        steps=[
            AiTaskStepRead(
                endpoint_id=step.endpoint_id,
                endpoint_name=step.endpoint_name,
                model_name=step.model_name,
            )
            for step in ai_tasks.chain_for(session, task)
        ],
    )
