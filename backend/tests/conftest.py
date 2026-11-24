"""Test fixtures.

Tests run against an isolated PostgreSQL database (TEST_DATABASE_URL, `alquilerapp_test` by
default). The database is created if missing and migrated to head once per session. Every test
runs inside a transaction that is rolled back, so tests never see each other's data.

APP_ENV is forced to "test" before the app is imported: same password algorithm (Argon2id),
cheaper parameters, so the suite stays fast.
"""

import os

os.environ["APP_ENV"] = "test"

import datetime as dt
import sys
import uuid
from collections.abc import Callable, Iterator

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Connection, Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.channels.email import Email, get_email_sender
from app.core import timezone as tz
from app.core.config import get_settings
from app.core.db import get_db, make_engine
from app.core.rate_limit import RateLimiter, get_rate_limiter
from app.main import create_app
from tests.db_utils import alembic_config, ensure_database_exists

get_settings.cache_clear()


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    settings = get_settings()
    test_url = settings.TEST_DATABASE_URL
    if make_url(test_url) == make_url(settings.DATABASE_URL):
        pytest.exit(
            "TEST_DATABASE_URL must differ from DATABASE_URL; refusing to run tests "
            "against the development database.",
            returncode=1,
        )

    ensure_database_exists(test_url)
    command.upgrade(alembic_config(test_url), "head")

    engine = make_engine(test_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(test_engine: Engine) -> Iterator[Connection]:
    connection = test_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    # Commits inside the code under test become savepoints; the outer transaction is rolled back.
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


class Outbox(list[Email]):
    """Emails "sent" during a test."""

    def send(self, email: Email) -> None:
        self.append(email)


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def rate_limiter() -> Iterator[RateLimiter]:
    """Real Redis, with a prefix unique to the test so counters never leak between tests."""
    redis = Redis.from_url(get_settings().REDIS_URL)
    limiter = RateLimiter(redis, prefix=f"test-rl-{uuid.uuid4().hex}")
    try:
        yield limiter
    finally:
        keys = list(redis.scan_iter(f"{limiter.prefix}:*"))
        if keys:
            redis.delete(*keys)
        redis.close()


@pytest.fixture
def app(db_session: Session, outbox: Outbox, rate_limiter: RateLimiter) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db_session
    application.dependency_overrides[get_email_sender] = lambda: outbox
    application.dependency_overrides[get_rate_limiter] = lambda: rate_limiter
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def freeze_time(monkeypatch: pytest.MonkeyPatch) -> Callable[[dt.datetime], None]:
    """Pin "now" everywhere utc_now() was imported (aware datetime, any zone)."""
    original = tz.utc_now

    def freeze(at: dt.datetime) -> None:
        fixed = at.astimezone(dt.UTC)
        for module in list(sys.modules.values()):
            if (
                module
                and module.__name__.startswith("app")
                and getattr(module, "utc_now", None) is original
            ):
                monkeypatch.setattr(module, "utc_now", lambda: fixed)

    return freeze
