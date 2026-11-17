"""Helpers to create and drop throwaway PostgreSQL databases in tests."""

from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import BACKEND_DIR


def _admin_engine(url: str):
    return create_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )


def ensure_database_exists(url: str) -> None:
    target = make_url(url)
    engine = _admin_engine(url)
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        engine.dispose()


def drop_database(url: str) -> None:
    target = make_url(url)
    engine = _admin_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
    finally:
        engine.dispose()


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config
