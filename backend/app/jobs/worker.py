"""RQ worker entry point: `python -m app.jobs.worker` (the `worker` service in docker-compose)."""

from rq import Worker

from app.jobs.queue import get_queue, get_redis


def main() -> None:
    connection = get_redis()
    Worker([get_queue(connection=connection)], connection=connection).work()


if __name__ == "__main__":
    main()
