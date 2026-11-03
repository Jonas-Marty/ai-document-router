from fastapi import APIRouter

from app.deps import AppSettingsDep, CurrentUserDep, SessionDep
from app.schemas import NextcloudRead, NextcloudWrite, SettingsRead, SettingsUpdate
from app.services import settings as settings_service
from app.services.audit import Actor

router = APIRouter()


@router.get("/settings")
def read_settings(app_settings: AppSettingsDep) -> SettingsRead:
    return settings_service.to_read_schema(app_settings)


@router.put("/settings")
def write_settings(
    payload: SettingsUpdate, session: SessionDep, user: CurrentUserDep, app_settings: AppSettingsDep
) -> SettingsRead:
    settings = settings_service.update_settings(
        session, app_settings, payload, actor=Actor.from_user(user)
    )
    return settings_service.to_read_schema(settings)


@router.get("/settings/nextcloud")
def read_nextcloud(app_settings: AppSettingsDep) -> NextcloudRead:
    return settings_service.read_nextcloud(app_settings)


@router.put("/settings/nextcloud")
def write_nextcloud(
    payload: NextcloudWrite, session: SessionDep, user: CurrentUserDep, app_settings: AppSettingsDep
) -> NextcloudRead:
    settings = settings_service.update_nextcloud(
        session, app_settings, payload, actor=Actor.from_user(user)
    )
    return settings_service.read_nextcloud(settings)


@router.post("/settings/nextcloud/test", status_code=204)
def test_nextcloud(payload: NextcloudWrite, app_settings: AppSettingsDep) -> None:
    """Sign in with the values in the form. 204 when they work, the reason when they do not."""
    settings_service.test_nextcloud(app_settings, payload)
