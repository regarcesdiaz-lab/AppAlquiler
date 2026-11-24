"""The one way to write a timeline event (append-only evidence, CLAUDE.md rule 5)."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.models import Lease, TimelineEvent
from app.models.enums import ActorType


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def record_event(
    db: Session,
    organization_id: uuid.UUID,
    event_type: str,
    *,
    lease: Lease | None = None,
    unit_id: uuid.UUID | None = None,
    actor: ActorType = ActorType.OWNER,
    user_id: uuid.UUID | None = None,
    **payload: Any,
) -> TimelineEvent:
    if user_id is not None:
        payload["user_id"] = user_id
    now = utc_now()
    event = TimelineEvent(
        organization_id=organization_id,
        lease_id=lease.id if lease else None,
        unit_id=lease.unit_id if lease else unit_id,
        event_type=event_type,
        actor_type=actor,
        payload=_jsonable(payload),
        occurred_at=now,
        created_at=now,
    )
    db.add(event)
    db.flush()
    return event
