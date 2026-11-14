from uuid import uuid4

from rq import SimpleWorker

from app.jobs.queue import DEFAULT_QUEUE, get_queue, get_redis


def double(value: int) -> int:
    return value * 2


def test_default_queue_name() -> None:
    assert get_queue().name == DEFAULT_QUEUE


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
