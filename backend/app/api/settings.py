from fastapi import APIRouter

from app.config import settings as app_config
from app.deps import CurrentUserDep, SessionDep
from app.schemas import AiModelsRequest, AiModelsResponse, SettingsRead, SettingsUpdate
from app.services import settings as settings_service

router = APIRouter()


@router.get("/settings")
def read_settings(session: SessionDep, _user: CurrentUserDep) -> SettingsRead:
    settings = settings_service.get_settings(session)
    return settings_service.to_read_schema(settings)


@router.put("/settings")
def write_settings(
    payload: SettingsUpdate, session: SessionDep, _user: CurrentUserDep
) -> SettingsRead:
    settings = settings_service.update_settings(session, payload, app_config.secret_key)
    return settings_service.to_read_schema(settings)


@router.post("/settings/ai/models")
def list_ai_models(
    payload: AiModelsRequest, session: SessionDep, _user: CurrentUserDep
) -> AiModelsResponse:
    models = settings_service.list_ai_models(session, payload.ai_endpoint_url, payload.ai_api_key)
    return AiModelsResponse(models=models)
