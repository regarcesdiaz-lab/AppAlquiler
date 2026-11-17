"""Timeline and ledger rows can never be edited or deleted (rule 5), except by account
deletion; checked through the ORM and through raw SQL."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import LedgerEntry, Organization, TimelineEvent
from app.models.guards import AppendOnlyError
from app.services.account import purge_organization
from tests.factories import make_charge, make_event, make_lease, make_org, make_payment


@pytest.fixture
def event(db_session: Session) -> TimelineEvent:
    return make_event(db_session, make_lease(db_session, make_org(db_session)))


@pytest.fixture
def entry(db_session: Session) -> LedgerEntry:
    return make_charge(db_session, make_lease(db_session, make_org(db_session)))


def test_orm_refuses_to_update_a_timeline_event(db_session: Session, event) -> None:
    with pytest.raises(AppendOnlyError), db_session.begin_nested():
        event.event_type = "edited"
        db_session.flush()


def test_orm_refuses_to_delete_a_timeline_event(db_session: Session, event) -> None:
    with pytest.raises(AppendOnlyError), db_session.begin_nested():
        db_session.delete(event)
        db_session.flush()


def test_orm_refuses_to_update_a_ledger_entry(db_session: Session, entry) -> None:
    with pytest.raises(AppendOnlyError), db_session.begin_nested():
        entry.amount = Decimal("1.00")
        db_session.flush()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE timeline_events SET event_type = 'edited' WHERE id = :id",
        "DELETE FROM timeline_events WHERE id = :id",
    ],
)
def test_database_refuses_raw_sql_on_timeline(db_session: Session, event, statement) -> None:
    with pytest.raises(IntegrityError, match="append-only"), db_session.begin_nested():
        db_session.execute(text(statement), {"id": event.id})


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger_entries SET amount = 1 WHERE id = :id",
        "DELETE FROM ledger_entries WHERE id = :id",
    ],
)
def test_database_refuses_raw_sql_on_ledger(db_session: Session, entry, statement) -> None:
    with pytest.raises(IntegrityError, match="append-only"), db_session.begin_nested():
        db_session.execute(text(statement), {"id": entry.id})


@pytest.mark.parametrize("table", ["timeline_events", "ledger_entries"])
def test_database_refuses_truncate(db_session: Session, event, entry, table) -> None:
    with pytest.raises(IntegrityError, match="append-only"), db_session.begin_nested():
        db_session.execute(text(f"TRUNCATE {table}"))


def test_deleting_an_organization_without_the_purge_is_refused(db_session: Session, event):
    with pytest.raises(IntegrityError, match="append-only"), db_session.begin_nested():
        db_session.execute(
            text("DELETE FROM organizations WHERE id = :id"), {"id": event.organization_id}
        )


def test_purge_deletes_one_organization_and_nothing_else(db_session: Session) -> None:
    doomed, kept = make_org(db_session), make_org(db_session)
    for org in (doomed, kept):
        lease = make_lease(db_session, org)
        make_charge(db_session, lease)
        make_payment(db_session, lease)
        make_event(db_session, lease)

    purge_organization(db_session, doomed.id)

    def count(model, org) -> int:
        return db_session.scalar(
            select(func.count()).select_from(model).where(model.organization_id == org.id)
        )

    assert db_session.get(Organization, doomed.id) is None
    assert count(TimelineEvent, doomed) == 0
    assert count(LedgerEntry, doomed) == 0
    assert count(TimelineEvent, kept) == 1
    assert count(LedgerEntry, kept) == 1

    # The purge flag does not outlive the purge.
    kept_event = db_session.scalar(
        select(TimelineEvent).where(TimelineEvent.organization_id == kept.id)
    )
    with pytest.raises(IntegrityError, match="append-only"), db_session.begin_nested():
        db_session.execute(
            text("DELETE FROM timeline_events WHERE id = :id"), {"id": kept_event.id}
        )
