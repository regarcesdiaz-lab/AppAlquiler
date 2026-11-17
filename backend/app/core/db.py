import datetime as dt
import decimal
import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import Date, DateTime, Engine, MetaData, Numeric, Time, Uuid, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# Deterministic constraint names, so Alembic migrations can reference (and drop) them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

# Money is always Decimal(12, 2); never float (CLAUDE.md, rule 1).
Money = Numeric(12, 2, asdecimal=True)


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012  (SQLAlchemy reads this class attribute)
        uuid.UUID: Uuid(),
        # timestamptz: stored as UTC by PostgreSQL; connections also use UTC (see make_engine).
        dt.datetime: DateTime(timezone=True),
        dt.date: Date(),
        dt.time: Time(),
        decimal.Decimal: Money,
        dict[str, Any]: JSONB(),
        list[Any]: JSONB(),
    }


def make_engine(url: str) -> Engine:
    # Every connection works in UTC, so timestamps read back as UTC-aware datetimes.
    return create_engine(url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})


engine = make_engine(get_settings().DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
