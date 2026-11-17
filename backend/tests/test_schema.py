"""Structural checks: the migration matches the models and the non-negotiable rules hold
for every table, including the ones added in the future."""

import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import (
    DateTime,
    Engine,
    Float,
    ForeignKeyConstraint,
    Numeric,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.db import Base, make_engine
from tests.db_utils import alembic_config, drop_database, ensure_database_exists

GLOBAL_TABLES = {"organizations"}
APPEND_ONLY_TABLES = {"timeline_events", "ledger_entries"}


def business_tables():
    return [t for name, t in Base.metadata.tables.items() if name not in GLOBAL_TABLES]


def test_migration_matches_models(test_engine: Engine) -> None:
    with test_engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_v1_tables_exist(test_engine: Engine) -> None:
    tables = set(inspect(test_engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert len(Base.metadata.tables) == 21


@pytest.mark.parametrize("table", business_tables(), ids=lambda t: t.name)
def test_business_table_is_scoped_to_an_organization(table) -> None:
    column = table.c.get("organization_id")
    assert column is not None and not column.nullable
    fk_targets = {fk.column.table.name for fk in column.foreign_keys}
    assert "organizations" in fk_targets
    # Target for composite foreign keys from other tables.
    unique_sets = [
        {c.name for c in constraint.columns}
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert {"organization_id", "id"} in unique_sets


@pytest.mark.parametrize("table", business_tables(), ids=lambda t: t.name)
def test_references_between_business_tables_include_the_organization(table) -> None:
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        if constraint.referred_table.name in GLOBAL_TABLES:
            continue
        columns = [c.name for c in constraint.columns]
        assert columns[0] == "organization_id", f"{table.name}: {columns} is not org-scoped"


def test_money_is_decimal_12_2_and_never_float() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, Float), f"{table.name}.{column.name} is a float"
            if isinstance(column.type, Numeric):
                assert (column.type.precision, column.type.scale) == (12, 2), (
                    f"{table.name}.{column.name}"
                )


def test_timestamps_are_timezone_aware() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone, f"{table.name}.{column.name} is not timestamptz"


def test_append_only_triggers_exist(test_engine: Engine) -> None:
    with test_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, t.tgname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal"
            )
        ).all()
    triggers = {(table, name) for table, name in rows}
    for table in APPEND_ONLY_TABLES:
        assert (table, f"{table}_append_only_rows") in triggers
        assert (table, f"{table}_append_only_truncate") in triggers


def test_migrations_downgrade_and_upgrade_cleanly() -> None:
    base_url = make_url(get_settings().TEST_DATABASE_URL)
    url = base_url.set(database=f"{base_url.database}_migrations_{uuid.uuid4().hex[:8]}")
    url_str = url.render_as_string(hide_password=False)
    ensure_database_exists(url_str)
    try:
        config = alembic_config(url_str)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        engine = make_engine(url_str)
        try:
            assert set(inspect(engine).get_table_names()) == {"alembic_version"}
            with engine.connect() as conn:
                leftover = conn.execute(
                    text("SELECT 1 FROM pg_proc WHERE proname = 'forbid_append_only_mutation'")
                ).first()
            assert leftover is None
        finally:
            engine.dispose()
        command.upgrade(config, "head")
    finally:
        drop_database(url_str)
