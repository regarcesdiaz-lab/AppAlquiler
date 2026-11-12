import logging
from typing import Annotated, Literal

import redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.core.config import get_settings
from app.core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: CheckStatus
    redis: CheckStatus
    version: str


def check_database(db: Session) -> CheckStatus:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database health check failed")
        return "error"
    return "ok"


def check_redis() -> CheckStatus:
    try:
        client = redis.Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=2)
        try:
            client.ping()
        finally:
            client.close()
    except Exception:
        logger.exception("Redis health check failed")
        return "error"
    return "ok"


@router.get("/health", response_model=HealthResponse, operation_id="getHealth")
def health(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        database=check_database(db),
        redis=check_redis(),
        version=__version__,
    )
