from redis import Redis
from rq import Queue

from app.core.config import get_settings

DEFAULT_QUEUE = "default"


def get_redis() -> Redis:
    return Redis.from_url(get_settings().REDIS_URL)


def get_queue(name: str = DEFAULT_QUEUE, connection: Redis | None = None) -> Queue:
    return Queue(name, connection=connection or get_redis())
