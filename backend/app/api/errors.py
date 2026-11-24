"""Error responses: ``{"detail": {"code": "...", "message": "..."}}`` with a Spanish message."""

from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


def api_error(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **extra},
        headers=headers,
    )


def not_found(message: str = "No encontramos lo que buscas.") -> HTTPException:
    return api_error(status.HTTP_404_NOT_FOUND, "not_found", message)


def conflict(code: str, message: str, **extra: Any) -> HTTPException:
    return api_error(status.HTTP_409_CONFLICT, code, message, **extra)


def unauthenticated(message: str = "Inicia sesión para continuar.") -> HTTPException:
    return api_error(
        status.HTTP_401_UNAUTHORIZED,
        "not_authenticated",
        message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def too_many_attempts(retry_after: int) -> HTTPException:
    minutes = max(1, round(retry_after / 60))
    unit = "minuto" if minutes == 1 else "minutos"
    return api_error(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "too_many_attempts",
        f"Demasiados intentos. Vuelve a intentarlo en {minutes} {unit}.",
        headers={"Retry-After": str(retry_after)},
    )


# OpenAPI documentation of the common error statuses.
COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not signed in"},
    404: {"model": ErrorResponse, "description": "Not found (or belongs to another organization)"},
}
