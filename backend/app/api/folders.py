from fastapi import APIRouter, Query

from app.deps import AppSettingsDep, CurrentUserDep, WebDavDep
from app.schemas import CreateFolderRequest, FolderContext, FolderNode
from app.services import folders as folders_service

router = APIRouter()


@router.get("/folders/tree")
def read_tree(
    _user: CurrentUserDep,
    webdav: WebDavDep,
    app_settings: AppSettingsDep,
    path: str | None = Query(None),
) -> list[FolderNode]:
    return folders_service.tree(webdav, app_settings, path)


@router.post("/folders")
def create_folder(
    payload: CreateFolderRequest,
    _user: CurrentUserDep,
    webdav: WebDavDep,
    app_settings: AppSettingsDep,
) -> FolderNode:
    return folders_service.create(webdav, app_settings, payload.parent_path, payload.name)


@router.get("/folders/context")
def read_context(
    _user: CurrentUserDep,
    webdav: WebDavDep,
    app_settings: AppSettingsDep,
    path: str = Query(...),
    filename: str | None = Query(None),
) -> FolderContext:
    return folders_service.context(webdav, app_settings, path, filename)
