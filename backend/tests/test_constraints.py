"""Business rules enforced by the database: multi-tenancy, uniqueness and consistency."""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.domain.rule_templates import RuleTemplate
from app.models import ConsentRecord, InboxItem, Issue, Message, PaymentPromise
from app.models.enums import (
    ConsentMethod,
    InboxItemType,
    LedgerEntryType,
    MessageChannel,
    MessageDirection,
    MessageStatus,
    PaymentSource,
    PaymentStatus,
    PromiseCreator,
)
from tests.factories import (
    add,
    make_charge,
    make_lease,
    make_org,
    make_payment,
    make_rule_set,
    make_tenant,
    make_unit,
    make_user,
)


def assert_rejected(db: Session, build) -> None:
    """The database rejects what `build` writes; the rest of the test can continue."""
    with pytest.raises(IntegrityError), db.begin_nested():
        build()
        db.flush()


# -- multi-tenancy -----------------------------------------------------------------------------


def test_lease_cannot_point_at_a_unit_of_another_organization(db_session: Session) -> None:
    org_a, org_b = make_org(db_session), make_org(db_session)
    foreign_unit = make_unit(db_session, org_b)
    tenant_a = make_tenant(db_session, org_a)

    assert_rejected(
        db_session,
        lambda: make_lease(db_session, org_a, unit_id=foreign_unit.id, tenant_id=tenant_a.id),
    )


def test_payment_cannot_be_confirmed_by_a_user_of_another_organization(
    db_session: Session,
) -> None:
    org_a, org_b = make_org(db_session), make_org(db_session)
    lease = make_lease(db_session, org_a)
    outsider = make_user(db_session, org_b)

    assert_rejected(
        db_session,
        lambda: make_payment(
            db_session,
            lease,
            status=PaymentStatus.CONFIRMED,
            confirmed_by_user_id=outsider.id,
            confirmed_at=utc_now(),
        ),
    )


def test_inbox_item_cannot_reference_a_payment_of_another_organization(
    db_session: Session,
) -> None:
    org_a, org_b = make_org(db_session), make_org(db_session)
    payment_b = make_payment(db_session, make_lease(db_session, org_b))

    assert_rejected(
        db_session,
        lambda: add(
            db_session,
            InboxItem(
                organization_id=org_a.id,
                payment_id=payment_b.id,
                type=InboxItemType.PAYMENT_TO_CONFIRM,
                summary="x",
            ),
        ),
    )


# -- "the AI never confirms a payment" ---------------------------------------------------------


def test_confirmed_payment_requires_a_user(db_session: Session) -> None:
    lease = make_lease(db_session, make_org(db_session))

    assert_rejected(
        db_session,
        lambda: make_payment(
            db_session, lease, status=PaymentStatus.CONFIRMED, confirmed_at=utc_now()
        ),
    )


def test_payment_confirmed_by_a_user_is_accepted(db_session: Session) -> None:
    org = make_org(db_session)
    lease = make_lease(db_session, org)
    owner = make_user(db_session, org)

    payment = make_payment(
        db_session,
        lease,
        status=PaymentStatus.CONFIRMED,
        confirmed_by_user_id=owner.id,
        confirmed_at=utc_now(),
    )

    assert payment.status == PaymentStatus.CONFIRMED


def test_new_payment_defaults_to_reported(db_session: Session) -> None:
    payment = make_payment(db_session, make_lease(db_session, make_org(db_session)))
    assert payment.status == PaymentStatus.REPORTED


def test_whatsapp_receipt_payment_needs_the_file(db_session: Session) -> None:
    lease = make_lease(db_session, make_org(db_session))
    assert_rejected(
        db_session,
        lambda: make_payment(db_session, lease, source=PaymentSource.WHATSAPP_RECEIPT),
    )


def test_money_keeps_exact_cents(db_session: Session) -> None:
    lease = make_lease(db_session, make_org(db_session), rent_amount=Decimal("18750.55"))
    db_session.expire(lease)
    assert lease.rent_amount == Decimal("18750.55")
    assert isinstance(lease.rent_amount, Decimal)


# -- uniqueness --------------------------------------------------------------------------------


def test_unit_has_at_most_one_active_lease(db_session: Session) -> None:
    org = make_org(db_session)
    unit = make_unit(db_session, org)
    make_lease(db_session, org, unit_id=unit.id)

    assert_rejected(db_session, lambda: make_lease(db_session, org, unit_id=unit.id))
    # An ended lease does not block the unit.
    make_lease(db_session, org, unit_id=unit.id, is_active=False)


def test_tenant_phone_is_unique_per_organization_only(db_session: Session) -> None:
    org_a, org_b = make_org(db_session), make_org(db_session)
    make_tenant(db_session, org_a, phone="+18095551234")

    make_tenant(db_session, org_b, phone="+18095551234")  # same person renting elsewhere
    assert_rejected(db_session, lambda: make_tenant(db_session, org_a, phone="+18095551234"))


def test_tenant_phone_must_be_e164(db_session: Session) -> None:
    org = make_org(db_session)
    assert_rejected(db_session, lambda: make_tenant(db_session, org, phone="809-555-1234"))


def test_provider_message_id_is_processed_once(db_session: Session) -> None:
    org = make_org(db_session)

    def inbound(provider_id: str | None) -> Message:
        return add(
            db_session,
            Message(
                organization_id=org.id,
                channel=MessageChannel.WHATSAPP,
                direction=MessageDirection.INBOUND,
                body="hola",
                status=MessageStatus.RECEIVED,
                provider_message_id=provider_id,
            ),
        )

    inbound("wamid.ABC")
    inbound(None)
    inbound(None)  # many messages without a provider id yet
    assert_rejected(db_session, lambda: inbound("wamid.ABC"))


def test_message_status_matches_direction(db_session: Session) -> None:
    org = make_org(db_session)
    assert_rejected(
        db_session,
        lambda: add(
            db_session,
            Message(
                organization_id=org.id,
                channel=MessageChannel.WHATSAPP,
                direction=MessageDirection.OUTBOUND,
                body="hola",
                status=MessageStatus.RECEIVED,
            ),
        ),
    )


def test_one_rent_charge_per_lease_and_period(db_session: Session) -> None:
    lease = make_lease(db_session, make_org(db_session))
    make_charge(db_session, lease, "2026-11")
    make_charge(db_session, lease, "2026-11", type=LedgerEntryType.LATE_FEE, amount=Decimal(500))

    assert_rejected(db_session, lambda: make_charge(db_session, lease, "2026-11"))


@pytest.mark.parametrize(
    ("entry_type", "amount"),
    [
        (LedgerEntryType.CHARGE, Decimal("-100.00")),
        (LedgerEntryType.LATE_FEE, Decimal("0.00")),
        (LedgerEntryType.ADJUSTMENT, Decimal("0.00")),
    ],
)
def test_ledger_amount_sign_matches_type(db_session: Session, entry_type, amount) -> None:
    lease = make_lease(db_session, make_org(db_session))
    assert_rejected(
        db_session, lambda: make_charge(db_session, lease, type=entry_type, amount=amount)
    )


def test_ledger_period_format(db_session: Session) -> None:
    lease = make_lease(db_session, make_org(db_session))
    assert_rejected(db_session, lambda: make_charge(db_session, lease, period="2026-13"))


def test_one_active_promise_per_lease(db_session: Session) -> None:
    org = make_org(db_session)
    lease = make_lease(db_session, org)

    def promise() -> PaymentPromise:
        return add(
            db_session,
            PaymentPromise(
                organization_id=org.id,
                lease_id=lease.id,
                promised_date=dt.date(2026, 11, 20),
                created_by=PromiseCreator.TENANT,
            ),
        )

    promise()
    assert_rejected(db_session, promise)


def test_one_current_consent_per_lease(db_session: Session) -> None:
    org = make_org(db_session)
    lease = make_lease(db_session, org)

    def consent(revoked: bool = False) -> ConsentRecord:
        now = utc_now()
        return add(
            db_session,
            ConsentRecord(
                organization_id=org.id,
                lease_id=lease.id,
                tenant_id=lease.tenant_id,
                method=ConsentMethod.WHATSAPP_REPLY,
                evidence="SI",
                granted_at=now,
                revoked_at=now if revoked else None,
            ),
        )

    consent(revoked=True)
    consent()
    assert_rejected(db_session, consent)


def test_inbox_item_points_at_most_at_one_entity(db_session: Session) -> None:
    org = make_org(db_session)
    lease = make_lease(db_session, org)
    payment = make_payment(db_session, lease)
    issue = add(db_session, Issue(organization_id=org.id, lease_id=lease.id, description="x"))

    assert_rejected(
        db_session,
        lambda: add(
            db_session,
            InboxItem(
                organization_id=org.id,
                lease_id=lease.id,
                payment_id=payment.id,
                issue_id=issue.id,
                type=InboxItemType.PAYMENT_TO_CONFIRM,
                summary="x",
            ),
        ),
    )


def test_unit_codes_are_unique_per_building_including_standalone_units(
    db_session: Session,
) -> None:
    org = make_org(db_session)
    make_unit(db_session, org, code="1A")
    assert_rejected(db_session, lambda: make_unit(db_session, org, code="1A"))


# -- collection rules (phase 2) ----------------------------------------------------------------


@pytest.mark.parametrize("template", list(RuleTemplate))
def test_every_preset_fits_the_rule_set_constraints(db_session: Session, template) -> None:
    rule_set = make_rule_set(db_session, make_org(db_session), template)
    assert rule_set.id is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"late_fee_cap_percent": Decimal("10.01")},  # above the platform cap
        {"late_fee_cap_percent": Decimal("0.00")},
        {"escalation_days": -1},
        {"max_reminders_per_day": 0},
        {"max_reminders_per_day": 2, "max_reminders_per_week": 1},
    ],
)
def test_rule_set_limits_are_enforced(db_session: Session, overrides) -> None:
    org = make_org(db_session)
    assert_rejected(db_session, lambda: make_rule_set(db_session, org, **overrides))


def test_late_fee_is_not_agreed_unless_the_lease_says_so(db_session: Session) -> None:
    lease = make_lease(db_session, make_org(db_session))
    db_session.refresh(lease)
    assert lease.late_fee_agreed is False


def test_inbox_accepts_overdue_escalations(db_session: Session) -> None:
    org = make_org(db_session)
    item = add(
        db_session,
        InboxItem(
            organization_id=org.id,
            lease_id=make_lease(db_session, org).id,
            type=InboxItemType.OVERDUE_ESCALATION,
            summary="x",
        ),
    )
    assert item.id is not None
