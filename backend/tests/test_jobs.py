from uuid import uuid4

from rq import SimpleWorker
from sqlalchemy.exc import ProgrammingError

from app.jobs import worker
from app.jobs.queue import DEFAULT_QUEUE, get_queue, get_redis


def double(value: int) -> int:
    return value * 2


def test_default_queue_name() -> None:
    assert get_queue().name == DEFAULT_QUEUE


def test_worker_starts_even_if_the_database_is_not_migrated(monkeypatch) -> None:
    # CI's docker job starts the worker on a fresh database (no migrations yet).
    def missing_table():
        raise ProgrammingError("SELECT messages.id", {}, Exception("relation does not exist"))

    monkeypatch.setattr(worker, "SessionLocal", missing_table)
    monkeypatch.setattr(worker, "get_queue", lambda: None)
    assert worker.requeue_due_messages() == 0


def test_worker_runs_an_enqueued_job() -> None:
    connection = get_redis()
    # Unique queue so the test never touches jobs in the development queue.
    queue = get_queue(f"test-{uuid4().hex}", connection=connection)
    try:
        job = queue.enqueue(double, 21)

        SimpleWorker([queue], connection=connection).work(burst=True)

        assert job.get_status(refresh=True) == "finished"
        assert job.return_value() == 42
    finally:
        queue.delete(delete_jobs=True)
        connection.close()
