"""Application settings.

Every value can be set through an environment variable or the `.env` file.
Secrets are typed as `SecretStr` so they do not appear in logs, reprs or
tracebacks by accident.
"""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# Used only on a developer machine. Production refuses to start with it.
DEV_SECRET_KEY = "dev-only-secret-key-change-me-before-you-go-live-0000"  # noqa: S105


class Settings(BaseSettings):
    # --- app -----------------------------------------------------------------
    APP_NAME: str = "Jobab"
    DEBUG: bool = False
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    # The address customers and Meta reach the app on. Used for cookie
    # security, webhook setup, the chat widget snippet and uploaded image links.
    PUBLIC_BASE_URL: str = "http://localhost:8002"
    # Signs nothing by itself, but every at-rest secret (channel tokens, store
    # API keys) is encrypted with a key derived from it.
    SECRET_KEY: SecretStr = SecretStr(DEV_SECRET_KEY)
    # Extra hostnames the app may be served on, comma separated.
    ALLOWED_HOSTS: str = ""
    API_DOCS_ENABLED: bool | None = None  # default: on outside production

    # --- database (same names as the course scaffold) -------------------------
    DB_HOST: str = "db"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: SecretStr = SecretStr("postgres")
    DB_NAME: str = "sales_agent"
    DB_SCHEMA: str = "sales_agent"

    # --- accounts and plans ---------------------------------------------------
    ALLOW_SIGNUP: bool = True
    ADMIN_EMAIL: str = ""  # this account sees the platform admin screens
    TRIAL_CREDITS: int = 300
    TRIAL_DAYS: int = 14
    SESSION_DAYS: int = 14

    # --- AI ---------------------------------------------------------------------
    AI_ENABLED: bool = True  # platform-wide switch; off = no AI replies anywhere
    LLM_BASE_URL: str = ""  # any OpenAI-compatible endpoint
    LLM_API_KEY: SecretStr = SecretStr("")
    LLM_MODEL: str = ""
    LLM_VISION: bool = False  # set true only when the model accepts images
    LLM_TIMEOUT_SECONDS: float = 45.0
    LLM_MAX_TOKENS: int = 700
    # Providers disagree on these two. Change them if your model rejects the
    # defaults: some want "max_completion_tokens", some refuse a temperature
    # (set LLM_TEMPERATURE to an empty value to leave it out).
    LLM_TOKEN_PARAM: str = "max_tokens"  # noqa: S105 - a field name, not a secret
    LLM_TEMPERATURE: float | None = 0.3
    # Optional: turns on meaning-based product search with pgvector.
    EMBEDDING_MODEL: str = ""
    EMBEDDING_BASE_URL: str = ""  # defaults to LLM_BASE_URL
    EMBEDDING_API_KEY: SecretStr = SecretStr("")  # defaults to LLM_API_KEY
    EMBEDDING_MAX_DISTANCE: float = 0.7
    STT_BASE_URL: str = ""  # optional: voice notes; defaults to LLM_BASE_URL
    STT_API_KEY: SecretStr = SecretStr("")
    STT_MODEL: str = ""

    # --- Meta (Messenger, Instagram, WhatsApp) -----------------------------------
    META_APP_SECRET: SecretStr = SecretStr("")
    META_VERIFY_TOKEN: SecretStr = SecretStr("")
    META_GRAPH_VERSION: str = "v26.0"
    META_GRAPH_BASE: str = "https://graph.facebook.com"
    META_IG_GRAPH_BASE: str = "https://graph.instagram.com"

    # --- behaviour ----------------------------------------------------------------
    # Wait this long for a customer to finish a burst of short messages before
    # the AI answers them together.
    REPLY_DEBOUNCE_SECONDS: float = 2.5
    UPLOAD_DIR: str = "data/uploads"
    # Outbound requests to private or loopback addresses are refused unless
    # this is true. Leave it false in production (SSRF protection).
    ALLOW_PRIVATE_URLS: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def DATABASE_URL(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD.get_secret_value(),
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def base_url(self) -> str:
        return self.PUBLIC_BASE_URL.rstrip("/")

    @property
    def cookie_secure(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def docs_enabled(self) -> bool:
        if self.API_DOCS_ENABLED is not None:
            return self.API_DOCS_ENABLED
        return not self.is_production

    @property
    def allowed_hosts(self) -> list[str]:
        hosts = {h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()}
        public_host = urlsplit(self.base_url).hostname
        if public_host:
            hosts.add(public_host)
        if not self.is_production:
            hosts.update({"localhost", "127.0.0.1", "testserver", "backend"})
        return sorted(hosts)

    @property
    def llm_configured(self) -> bool:
        return bool(self.LLM_BASE_URL and self.LLM_MODEL and self.LLM_API_KEY.get_secret_value())

    @property
    def stt_configured(self) -> bool:
        return bool(self.STT_MODEL and (self.STT_BASE_URL or self.LLM_BASE_URL))

    @property
    def meta_configured(self) -> bool:
        return bool(
            self.META_APP_SECRET.get_secret_value() and self.META_VERIFY_TOKEN.get_secret_value()
        )

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> "Settings":
        """Fail fast instead of running in production with developer defaults."""
        if not self.is_production:
            return self
        problems = []
        secret = self.SECRET_KEY.get_secret_value()
        if secret == DEV_SECRET_KEY or len(secret) < 32:
            problems.append("SECRET_KEY must be a random string of at least 32 characters")
        if self.DEBUG:
            problems.append("DEBUG must be false")
        if not self.base_url.startswith("https://"):
            problems.append("PUBLIC_BASE_URL must start with https://")
        if self.DB_PASSWORD.get_secret_value() in {"", "postgres", "password"}:
            problems.append("DB_PASSWORD must not be a default password")
        if self.ALLOW_PRIVATE_URLS:
            problems.append("ALLOW_PRIVATE_URLS must be false")
        if problems:
            raise ValueError("Unsafe production settings: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
