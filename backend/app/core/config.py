from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

MIN_SECRET_KEY_LENGTH = 32
# Not a secret: only used outside production when SECRET_KEY is empty (production refuses it).
DEVELOPMENT_SIGNING_KEY = "insecure-development-key-never-use-in-production"


class Settings(BaseSettings):
    """Application settings, read from environment variables (and `backend/.env` locally)."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    APP_ENV: Literal["development", "test", "production"] = "development"

    DATABASE_URL: str = "postgresql+psycopg://alquilerapp:alquilerapp@localhost:5432/alquilerapp"
    TEST_DATABASE_URL: str = (
        "postgresql+psycopg://alquilerapp:alquilerapp@localhost:5432/alquilerapp_test"
    )
    REDIS_URL: str = "redis://localhost:6379/0"

    # Only the owner's web panel calls the API from a browser (Expo web on 8081 in development).
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:8081"]
    )

    # Signs access tokens (JWT) and CSRF tokens. Required (32+ characters) in production.
    SECRET_KEY: str = ""
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 60
    SESSION_IDLE_DAYS: int = 7
    SESSION_ABSOLUTE_DAYS: int = 30
    MAGIC_LINK_TTL_MINUTES: int = 15
    # Web panel base URL: magic links point here (the app opens them through universal links).
    PANEL_URL: str = "http://localhost:8081"
    # Custom scheme of the app (mobile/app.json "scheme"), used for development deep links.
    APP_SCHEME: str = "alquilerapp"
    # Secure cookies need HTTPS; unset means "only in production".
    COOKIE_SECURE: bool | None = None

    # All rent-collection date logic uses this zone; timestamps are stored in UTC.
    TIMEZONE: str = "America/Santo_Domingo"

    # Real outbound messages are only sent when this is "live".
    MESSAGING_MODE: Literal["simulator", "live"] = "simulator"

    ANTHROPIC_API_KEY: str = ""
    LLM_MODEL: str = "claude-sonnet-5-5"

    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""

    EMAIL_PROVIDER: Literal["console", "resend", "postmark", "ses"] = "console"
    EMAIL_API_KEY: str = ""
    EMAIL_FROM: str = ""

    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_PATH: str = "storage"
    S3_BUCKET: str = ""
    S3_REGION: str = ""
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def cookie_secure(self) -> bool:
        return self.is_production if self.COOKIE_SECURE is None else self.COOKIE_SECURE

    @property
    def signing_key(self) -> str:
        # Outside production an empty key falls back to a fixed, public development value.
        return self.SECRET_KEY or DEVELOPMENT_SIGNING_KEY

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _production_needs_a_secret(self) -> "Settings":
        if self.is_production and len(self.SECRET_KEY) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(f"SECRET_KEY must have at least {MIN_SECRET_KEY_LENGTH} characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
