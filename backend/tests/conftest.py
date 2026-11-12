"""Test fixtures.

Tests run against an isolated PostgreSQL database (TEST_DATABASE_URL, `alquilerapp_test` by
default). The database is created if missing and migrated to head once per session. Every test
runs inside a transaction that is rolled back, so tests never see each other's data.
"""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR, get_settings
from app.core.db import get_db
from app.main import create_app


def _ensure_database_exists(url: str) -> None:
    target = make_url(url)
    admin_engine = create_engine(
        target.set(database="postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin_engine.dispose()


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

    _ensure_database_exists(test_url)

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", test_url.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(test_url, pool_pre_ping=True)
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


@pytest.fixture
def app(db_session: Session) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db_session
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
