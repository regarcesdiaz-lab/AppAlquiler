from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


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

    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:8081", "http://localhost:3000"]
    )

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

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
