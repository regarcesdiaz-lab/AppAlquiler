"""RQ worker entry point: `python -m app.jobs.worker` (the `worker` service in docker-compose).

Runs with RQ's scheduler so messages scheduled for the next contact window (``enqueue_at``) go
out on time. On start it re-queues queued messages that are already due, in case their job was
lost (Redis flushed, worker down): sending is idempotent, a message leaves ``queued`` once.
"""

import logging

from rq import Worker
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import SessionLocal
from app.core.timezone import utc_now
from app.jobs.queue import get_queue, get_redis
from app.jobs.runner import RETRY, run_job
from app.models import Message
from app.models.enums import MessageDirection, MessageStatus

logger = logging.getLogger(__name__)


def requeue_due_messages() -> int:
    """Best effort: the worker must start even if the database isn't migrated or reachable."""
    queue = get_queue()
    try:
        with SessionLocal() as db:
            ids = db.scalars(
                select(Message.id).where(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.status == MessageStatus.QUEUED,
                    or_(Message.scheduled_for.is_(None), Message.scheduled_for <= utc_now()),
                )
            ).all()
    except SQLAlchemyError:
        logger.warning("Could not re-queue due WhatsApp messages (database not ready?)")
        return 0
    for message_id in ids:
        queue.enqueue(run_job, "send_message", str(message_id), retry=RETRY)
    if ids:
        logger.info("Re-queued %d due WhatsApp messages", len(ids))
    return len(ids)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    connection = get_redis()
    requeue_due_messages()
    Worker([get_queue(connection=connection)], connection=connection).work(with_scheduler=True)


if __name__ == "__main__":
    main()
