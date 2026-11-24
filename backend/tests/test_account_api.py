"""Profile, password change, account deletion, devices, onboarding and settings."""

import datetime as dt
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import sha256_hex
from app.models import (
    AccountDeletionRequest,
    DeviceToken,
    Lease,
    LedgerEntry,
    Organization,
    RefreshToken,
    RuleSet,
    TimelineEvent,
    User,
)
from app.services.timeline import record_event
from tests.api_utils import PASSWORD, make_owner, make_portfolio, ok

TOKEN = "ExponentPushToken[abc123XYZ]"


def test_me_and_profile_update(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    response = ok(
        client,
        "PATCH",
        "/v1/me",
        owner.headers,
        json={"full_name": "Carmen R.", "whatsapp_phone": "809 555 0100"},
    )
    assert response.json()["whatsapp_phone"] == "+18095550100"
    assert response.json()["has_password"] is False


def test_change_password_needs_current_password_or_recent_sign_in(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session, with_password=True)
    # The token was issued just now: a recent sign-in is enough.
    ok(client, "POST", "/v1/me/password", owner.headers, json={"new_password": "nueva-clave-larga"})

    family = db_session.scalar(select(RefreshToken).where(RefreshToken.user_id == owner.user.id))
    family.authenticated_at -= dt.timedelta(hours=1)
    db_session.flush()
    stale = client.post(
        "/v1/me/password", headers=owner.headers, json={"new_password": "otra-clave-larga"}
    )
    assert stale.status_code == 403
    assert stale.json()["detail"]["code"] == "reauthentication_required"
    wrong = client.post(
        "/v1/me/password",
        headers=owner.headers,
        json={"current_password": PASSWORD, "new_password": "otra-clave-larga"},
    )
    assert wrong.status_code == 403  # the password already changed above
    ok(
        client,
        "POST",
        "/v1/me/password",
        owner.headers,
        json={"current_password": "nueva-clave-larga", "new_password": "otra-clave-larga"},
    )


def test_account_deletion_purges_everything_and_keeps_the_request(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session, with_password=True)
    other = make_owner(db_session)
    portfolio = make_portfolio(db_session, owner.org)
    make_portfolio(db_session, other.org)
    record_event(db_session, owner.org.id, "test.event", lease=portfolio.lease)
    org_id, email = owner.org.id, owner.user.email

    refused = client.request(
        "DELETE", "/v1/me/account", headers=owner.headers, json={"confirm": "BORRAR"}
    )
    assert refused.status_code == 422
    response = ok(
        client,
        "DELETE",
        "/v1/me/account",
        owner.headers,
        json={"password": PASSWORD, "confirm": "ELIMINAR"},
    )
    assert response.json()["deleted"] is True

    assert db_session.get(Organization, org_id) is None
    for model in (User, Lease, LedgerEntry, TimelineEvent, RuleSet):
        count = db_session.scalar(
            select(func.count()).select_from(model).where(model.organization_id == org_id)
        )
        assert count == 0, model.__name__
    request = db_session.scalar(
        select(AccountDeletionRequest).where(AccountDeletionRequest.organization_id == org_id)
    )
    assert request.completed_at is not None
    assert request.email_sha256 == sha256_hex(email)
    # The other organization is untouched.
    assert db_session.get(Organization, other.org.id) is not None
    assert (
        db_session.scalar(
            select(func.count()).select_from(Lease).where(Lease.organization_id == other.org.id)
        )
        == 1
    )
    assert client.get("/v1/me", headers=owner.headers).status_code == 401


def test_devices_register_upsert_and_delete(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    other = make_owner(db_session)
    ok(
        client,
        "POST",
        "/v1/devices",
        owner.headers,
        json={"expo_push_token": TOKEN, "platform": "ios"},
    )
    ok(
        client,
        "POST",
        "/v1/devices",
        owner.headers,
        json={"expo_push_token": TOKEN, "platform": "ios"},
    )
    assert db_session.scalar(select(func.count()).select_from(DeviceToken)) == 1
    bad = client.post(
        "/v1/devices", headers=owner.headers, json={"expo_push_token": "nope", "platform": "ios"}
    )
    assert bad.status_code == 422
    # Same phone, other account: the token moves.
    ok(
        client,
        "POST",
        "/v1/devices",
        other.headers,
        json={"expo_push_token": TOKEN, "platform": "ios"},
    )
    assert client.delete(f"/v1/devices/{TOKEN}", headers=owner.headers).status_code == 404
    assert client.delete(f"/v1/devices/{TOKEN}", headers=other.headers).status_code == 204


def test_onboarding_steps_follow_the_data(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    steps = {
        s["key"]: s["done"]
        for s in ok(client, "GET", "/v1/onboarding", owner.headers).json()["steps"]
    }
    assert steps == {
        "organization": True,
        "assistant": False,
        "rules": False,
        "bank_accounts": False,
        "first_property": False,
    }
    ok(
        client,
        "PUT",
        "/v1/settings/assistant",
        owner.headers,
        json={"display_name": "Lucía", "tone": "cordial"},
    )
    ok(client, "PUT", "/v1/settings/rule-set", owner.headers, json={"template": "standard"})
    ok(
        client,
        "POST",
        "/v1/settings/bank-accounts",
        owner.headers,
        json={
            "bank_name": "Banreservas",
            "account_type": "checking",
            "account_number": "1234567890",
            "holder_name": "Carmen",
        },
    )
    ok(client, "POST", "/v1/units", owner.headers, json={"code": "Casa"})
    body = ok(client, "GET", "/v1/onboarding", owner.headers).json()
    assert body["completed"] is True


def test_rule_set_from_template_with_overrides_and_validation(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    assert client.get("/v1/settings/rule-set", headers=owner.headers).status_code == 404
    body = ok(
        client,
        "PUT",
        "/v1/settings/rule-set",
        owner.headers,
        json={"template": "strict", "grace_days": 2},
    ).json()
    assert body["template"] == "strict" and body["grace_days"] == 2
    assert body["late_fee_frequency"] == "daily" and body["is_default"] is True
    body = ok(client, "PUT", "/v1/settings/rule-set", owner.headers, json={"grace_days": 4}).json()
    assert body["grace_days"] == 4 and body["late_fee_frequency"] == "daily"

    for invalid in (
        {"contact_days": [1, 7]},  # never Sundays
        {"late_fee_type": "none"},  # value must then be 0
        {"late_fee_cap_percent": "15"},  # platform cap is 10 %
        {"contact_start": "19:00:00", "contact_end": "08:00:00"},
        {
            "max_reminders_per_day": 1,
            "reminder_sequence": [
                {"offset_days": 0, "template": "a"},
                {"offset_days": 0, "template": "b"},
            ],
        },
    ):
        response = client.put("/v1/settings/rule-set", headers=owner.headers, json=invalid)
        assert response.status_code == 422, invalid
    events = db_session.scalars(
        select(TimelineEvent.event_type).where(TimelineEvent.organization_id == owner.org.id)
    ).all()
    assert events.count("rules.updated") == 2


def test_bank_accounts_crud_and_soft_delete(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    payload = {
        "bank_name": "Banco Popular",
        "account_type": "savings",
        "account_number": "7801234567",
        "holder_name": "Carmen",
    }
    created = ok(client, "POST", "/v1/settings/bank-accounts", owner.headers, json=payload).json()
    assert (
        client.post("/v1/settings/bank-accounts", headers=owner.headers, json=payload).status_code
        == 409
    )
    ok(
        client,
        "PATCH",
        f"/v1/settings/bank-accounts/{created['id']}",
        owner.headers,
        json={"alias": "Ahorros"},
    )
    ok(client, "DELETE", f"/v1/settings/bank-accounts/{created['id']}", owner.headers)
    assert ok(client, "GET", "/v1/settings/bank-accounts", owner.headers).json() == []
    everything = ok(
        client, "GET", "/v1/settings/bank-accounts?include_inactive=true", owner.headers
    ).json()
    assert everything[0]["is_active"] is False and everything[0]["alias"] == "Ahorros"


def test_money_is_serialized_as_strings(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    body = ok(
        client, "PUT", "/v1/settings/rule-set", owner.headers, json={"template": "standard"}
    ).json()
    assert body["late_fee_value"] == "5.00"
    assert Decimal(body["late_fee_value"]) == Decimal("5")
