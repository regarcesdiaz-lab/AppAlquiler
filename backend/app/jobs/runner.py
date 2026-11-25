"""Background jobs that touch the database, enqueued only after the transaction commits.

Services call ``schedule(db, "send_message", message_id, at=...)``. With the RQ runner (default)
the job is held in ``db.info`` and pushed to Redis right after ``db.commit()``, so the worker
never looks for a row that isn't committed yet; a rollback drops it. ``at`` uses RQ's scheduler
(the worker runs with ``with_scheduler=True``).

Tests swap in ``InlineJobRunner``: due jobs run immediately inside the test's transaction and
future ones are only recorded, so a test can assert on them and run them by hand.
"""

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from rq import Retry
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.jobs.queue import get_queue

logger = logging.getLogger(__name__)

JobFn = Callable[..., None]  # fn(db, *args)
_REGISTRY: dict[str, JobFn] = {}
PENDING_KEY = "pending_jobs"
RETRY = Retry(max=3, interval=[10, 60, 300])


def job(name: str) -> Callable[[JobFn], JobFn]:
    def register(fn: JobFn) -> JobFn:
        _REGISTRY[name] = fn
        return fn

    return register


def registered(name: str) -> JobFn:
    import app.jobs.registry  # noqa: F401  (registers every job)

    return _REGISTRY[name]


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    args: tuple[Any, ...]
    at: dt.datetime | None


class JobRunner(Protocol):
    def schedule(self, db: Session, job: ScheduledJob) -> None: ...


def run_job(name: str, *args: Any) -> None:
    """RQ entry point: own session, commit at the end (which may enqueue follow-up jobs)."""
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        registered(name)(db, *args)
        db.commit()


class RQJobRunner:
    def schedule(self, db: Session, job: ScheduledJob) -> None:
        db.info.setdefault(PENDING_KEY, []).append(job)


@event.listens_for(Session, "after_commit")
def _enqueue_after_commit(session: Session) -> None:
    pending: list[ScheduledJob] = session.info.pop(PENDING_KEY, [])
    if not pending:
        return
    queue = get_queue()
    for item in pending:
        if item.at is not None and item.at > utc_now():
            queue.enqueue_at(item.at, run_job, item.name, *item.args, retry=RETRY)
        else:
            queue.enqueue(run_job, item.name, *item.args, retry=RETRY)


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop(PENDING_KEY, None)


@dataclass
class InlineJobRunner:
    """Tests: run due jobs now, in the caller's session; keep future ones for inspection."""

    future: list[ScheduledJob] = field(default_factory=list)
    ran: list[ScheduledJob] = field(default_factory=list)

    def schedule(self, db: Session, job: ScheduledJob) -> None:
        if job.at is not None and job.at > utc_now():
            self.future.append(job)
            return
        self.ran.append(job)
        registered(job.name)(db, *job.args)

    def run_future(self, db: Session) -> None:
        jobs, self.future = self.future, []
        for item in jobs:
            self.ran.append(item)
            registered(item.name)(db, *item.args)


_runner: JobRunner = RQJobRunner()


def set_job_runner(runner: JobRunner | None) -> None:
    global _runner
    _runner = runner or RQJobRunner()


def get_job_runner() -> JobRunner:
    return _runner


def schedule(db: Session, name: str, *args: Any, at: dt.datetime | None = None) -> None:
    _runner.schedule(db, ScheduledJob(name, tuple(args), at))
