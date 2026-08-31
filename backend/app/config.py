from typing import Annotated

from cryptography.fernet import Fernet
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
    # Much smaller than the others on purpose: OCRing a whole document is tens of seconds
    # where a proposal is a few, and a tick that spent minutes in ocrmypdf would leave newly
    # scanned files unnoticed. The backlog drains over subsequent ticks.
    poller_ocr_batch: int = 2
    # SPEC 6.2: ignore anything written within the last few seconds, in case the scanner is
    # still uploading it.
    poller_min_file_age_seconds: int = 10

    # --- ocr ----------------------------------------------------------------
    # Where a searchable copy waits between the poller producing it and approve filing it.
    # Under the same volume as the database, because a copy that vanished on restart would
    # silently file the original instead -- losing the text layer with nothing to show for it.
    ocr_cache_dir: str = "./data/ocr"
    # Generous: a 20-page scan on a CPU-only container is minutes, and a timeout here means
    # the document is filed without a text layer, which is worse than waiting.
    ocr_timeout_seconds: float = 600.0
    # A cached copy nobody claimed is a document that was trashed, or one sitting skipped
    # forever. Bounded by age rather than count so the cache cannot grow without limit.
    ocr_cache_max_age_days: int = 14

    # --- auth ---------------------------------------------------------------
    # Where the browser reaches this app. Only used to build the OIDC redirect URI and to
    # decide whether the session cookie may be marked Secure, so it must be the *public*
    # URL, not the container's.
    app_base_url: str = "http://localhost:5173"
    session_lifetime_days: int = 30
    # The first account is always allowed -- someone has to claim a fresh instance. This
    # governs everyone after them; off by default, because a self-hosted single-user tool
    # with open registration is an unlocked front door.
    allow_registration: bool = False

    # A single OIDC provider, confidential client. All three must be set for the SSO button
    # to appear; a client without a secret is deliberately not supported.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid email profile"
    oidc_provider_name: str = "SSO"

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    @property
    def session_cookie_secure(self) -> bool:
        return self.app_base_url.startswith("https://")

    @field_validator("secret_key")
    @classmethod
    def check_secret_key_is_a_fernet_key(cls, value: str) -> str:
        """Fail at startup, not at the one request that happens to need the key.

        SECRET_KEY is only ever used to encrypt the AI API key (services/crypto.py), so an
        invalid one is invisible through boot, the health check, and every other endpoint --
        and then surfaces as a 500 the first time someone saves a key in Settings. Checking it
        here makes a bad value a container that refuses to start, which is what
        deploy/docker-compose.dokploy.yml already promises for a missing one.
        """
        try:
            Fernet(value.encode())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "must be a Fernet key -- 32 url-safe base64-encoded bytes, i.e. 44 characters "
                "ending in '='. Generate one with `just secret-key`."
            ) from exc
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
