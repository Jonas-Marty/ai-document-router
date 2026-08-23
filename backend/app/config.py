from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "sqlite:///./data/app.db"
    secret_key: str
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    log_level: str = "INFO"

    # WebDAV credentials live in env, not the settings table: they are infrastructure, and
    # the app must be able to start (and report itself unhealthy) without them.
    webdav_base_url: str = ""
    webdav_username: str = ""
    webdav_password: str = ""
    webdav_watch_folder: str = "/Scans/Inbox"
    webdav_timeout_seconds: float = 30.0
    # The health probe gets its own short timeout: the frontend polls /health on an interval
    # and an outage banner that takes 30s to appear is worse than no banner.
    webdav_health_timeout_seconds: float = 2.0

    poll_interval_seconds: int = 60

    # Off in tests: the TestClient runs the lifespan, which would otherwise start a real
    # background scheduler doing real network I/O during the suite.
    poller_enabled: bool = True
    # A first run against a populated watch folder would otherwise download every file and
    # make one LLM call per document in a single tick. Caps keep each tick bounded; the
    # backlog drains over subsequent ticks.
    poller_ingest_batch: int = 20
    poller_proposal_batch: int = 5
    # SPEC 6.2: ignore anything written within the last few seconds, in case the scanner is
    # still uploading it.
    poller_min_file_age_seconds: int = 10

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
