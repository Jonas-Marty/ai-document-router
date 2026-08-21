from pydantic import BaseModel


class SettingsRead(BaseModel):
    allowed_root_folders: list[str]
    trash_folder_path: str
    filename_pattern: str | None
    filename_pattern_hint: str | None
    ai_endpoint_url: str
    ai_model_name: str
    ai_api_key_set: bool


class SettingsUpdate(BaseModel):
    allowed_root_folders: list[str]
    trash_folder_path: str
    filename_pattern: str | None = None
    filename_pattern_hint: str | None = None
    ai_endpoint_url: str
    ai_model_name: str
    ai_api_key: str | None = None
