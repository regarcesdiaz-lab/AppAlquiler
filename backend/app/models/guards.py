"""ORM guard for append-only tables (the database trigger is the real enforcement)."""

from typing import Any

from sqlalchemy import event

from app.models.base import AppendOnlyMixin


class AppendOnlyError(RuntimeError):
    """Raised when code tries to modify or delete an append-only row."""


def _forbid(operation: str):
    def listener(_mapper: Any, _connection: Any, target: Any) -> None:
        raise AppendOnlyError(f"{type(target).__name__} is append-only: {operation} is not allowed")

    return listener


event.listen(AppendOnlyMixin, "before_update", _forbid("UPDATE"), propagate=True)
event.listen(AppendOnlyMixin, "before_delete", _forbid("DELETE"), propagate=True)
