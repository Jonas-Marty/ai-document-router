"""Connections (AI endpoints) and what they were used for (SPEC 6.3a, 8.7, 8.12)."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter

from app.deps import CurrentUserDep, SessionDep
from app.models import AiEndpoint
from app.schemas import (
    AiEndpointRead,
    AiEndpointWrite,
    AiModelPrice,
    AiModelsRequest,
    AiModelsResponse,
    AiUsageRead,
    AiUsageRowRead,
    EndpointStatusRead,
)
from app.services import ai_usage, connections
from app.services.audit import Actor

router = APIRouter()


@router.get("/ai/endpoints")
def list_endpoints(session: SessionDep, user: CurrentUserDep) -> list[AiEndpointRead]:
    return [_read(session, endpoint) for endpoint in connections.list_endpoints(session, user.id)]


@router.get("/ai/endpoints/status")
def endpoint_statuses(session: SessionDep, user: CurrentUserDep) -> list[EndpointStatusRead]:
    """Whether each connection answers, for the header (SPEC 6.4b)."""
    return [
        EndpointStatusRead(id=status.id, name=status.name, reachable=status.reachable)
        for status in connections.statuses(session, user.id)
    ]


@router.post("/ai/endpoints")
def create_endpoint(
    payload: AiEndpointWrite, session: SessionDep, user: CurrentUserDep
) -> AiEndpointRead:
    endpoint = connections.create_endpoint(
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
    endpoint = connections.update_endpoint(
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


def _capabilities(payload: AiEndpointWrite) -> connections.Capabilities:
    return connections.Capabilities(
        context_limit=payload.context_limit,
        json_mode=payload.json_mode,
        model_prices=tuple(
            connections.ModelPrice(
                price.model_name, price.price_input_per_million, price.price_output_per_million
            )
            for price in payload.model_prices
        ),
    )


@router.delete("/ai/endpoints/{endpoint_id}")
def delete_endpoint(endpoint_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    connections.delete_endpoint(session, user.id, endpoint_id, actor=Actor.from_user(user))


@router.post("/ai/models")
def list_models(
    payload: AiModelsRequest, session: SessionDep, user: CurrentUserDep
) -> AiModelsResponse:
    models = connections.list_models(
        session, user.id, payload.base_url, payload.api_key, payload.endpoint_id
    )
    return AiModelsResponse(models=models)


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
        model_prices=[
            AiModelPrice(
                model_name=price.model_name,
                price_input_per_million=price.price_input_per_million,
                price_output_per_million=price.price_output_per_million,
            )
            for price in connections.Capabilities.of(endpoint).model_prices
        ],
        used_by=connections.endpoint_in_use(session, endpoint.id),
    )
