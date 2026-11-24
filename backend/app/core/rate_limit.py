"""Fixed-window attempt counters in Redis (login, magic links, registration, refresh).

If Redis is unreachable the limiter fails open (logs the error) so a Redis outage never locks
every owner out; the password hash cost and the other limits still apply.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    name: str
    max_attempts: int
    window_seconds: int


LOGIN_FAILURES_PER_EMAIL = Limit("login-email", 5, 15 * 60)
LOGIN_PER_IP = Limit("login-ip", 30, 15 * 60)
MAGIC_LINK_PER_EMAIL = Limit("magic-email", 3, 60 * 60)
MAGIC_LINK_PER_IP = Limit("magic-ip", 10, 60 * 60)
REGISTER_PER_IP = Limit("register-ip", 10, 60 * 60)
REFRESH_PER_IP = Limit("refresh-ip", 60, 60)


class RateLimitExceededError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, redis: Redis, prefix: str = "rl") -> None:
        self.redis = redis
        self.prefix = prefix

    def _key(self, limit: Limit, identity: str) -> str:
        return f"{self.prefix}:{limit.name}:{identity.lower()}"

    def check(self, limit: Limit, identity: str) -> None:
        """Raise if the counter is already at the limit (does not count this attempt)."""
        try:
            key = self._key(limit, identity)
            count = int(self.redis.get(key) or 0)
            if count >= limit.max_attempts:
                ttl = self.redis.ttl(key)
                raise RateLimitExceededError(ttl if ttl and ttl > 0 else limit.window_seconds)
        except RedisError:
            logger.exception("Rate limiter unavailable; allowing the request")

    def hit(self, limit: Limit, identity: str) -> None:
        """Count one attempt; raise if it goes over the limit."""
        try:
            key = self._key(limit, identity)
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, limit.window_seconds, nx=True)
            count, _ = pipe.execute()
            if int(count) > limit.max_attempts:
                ttl = self.redis.ttl(key)
                raise RateLimitExceededError(ttl if ttl and ttl > 0 else limit.window_seconds)
        except RedisError:
            logger.exception("Rate limiter unavailable; allowing the request")

    def reset(self, limit: Limit, identity: str) -> None:
        try:
            self.redis.delete(self._key(limit, identity))
        except RedisError:
            logger.exception("Rate limiter unavailable; could not reset a counter")


def get_rate_limiter() -> Iterator[RateLimiter]:
    redis = Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    try:
        yield RateLimiter(redis)
    finally:
        redis.close()
