"""Properties CRUD, lease creation rules, the detail screen and the timeline."""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConsentRecord, Lease, TimelineEvent
from app.models.enums import ConsentMethod
from tests.api_utils import lease_payload, make_owner, make_portfolio, ok
from tests.factories import make_event


def test_building_unit_tenant_crud_and_conflicts(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    h = owner.headers
    building = ok(client, "POST", "/v1/buildings", h, json={"name": "Torre A"}).json()
    assert client.post("/v1/buildings", headers=h, json={"name": "Torre A"}).status_code == 409
    unit = ok(
        client, "POST", "/v1/units", h, json={"building_id": building["id"], "code": "101"}
    ).json()
    assert (
        client.post(
            "/v1/units", headers=h, json={"building_id": building["id"], "code": "101"}
        ).status_code
        == 409
    )
    assert ok(client, "GET", f"/v1/buildings/{building['id']}", h).json()["unit_count"] == 1
    blocked = client.delete(f"/v1/buildings/{building['id']}", headers=h)
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "building_has_units"

    tenant = ok(
        client, "POST", "/v1/tenants", h, json={"full_name": "Ana Peña", "phone": "849 555 0103"}
    ).json()
    assert tenant["tenant"]["phone"] == "+18495550103" and tenant["warnings"] == []
    assert (
        client.post(
            "/v1/tenants", headers=h, json={"full_name": "Otra", "phone": "+18495550103"}
        ).status_code
        == 409
    )
    assert ok(client, "GET", "/v1/tenants?q=ana", h).json()[0]["id"] == tenant["tenant"]["id"]

    ok(client, "DELETE", f"/v1/units/{unit['id']}", h)
    ok(client, "DELETE", f"/v1/buildings/{building['id']}", h)
    ok(client, "DELETE", f"/v1/tenants/{tenant['tenant']['id']}", h)


def test_tenant_phone_outside_dominican_area_codes_warns_without_blocking(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    created = ok(
        client,
        "POST",
        "/v1/tenants",
        owner.headers,
        json={"full_name": "Turista", "phone": "305-555-0101"},
    ).json()
    assert created["tenant"]["phone"] == "+13055550101"
    assert [w["code"] for w in created["warnings"]] == ["phone_not_do_area_code"]
    updated = ok(
        client,
        "PATCH",
        f"/v1/tenants/{created['tenant']['id']}",
        owner.headers,
        json={"phone": "212 555 0101"},
    ).json()
    assert [w["code"] for w in updated["warnings"]] == ["phone_not_do_area_code"]


def test_create_lease_requires_consent_and_the_late_fee_answer(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    unit = ok(client, "POST", "/v1/units", owner.headers, json={"code": "2B"}).json()

    no_consent = client.post(
        "/v1/leases", headers=owner.headers, json=lease_payload(unit["id"], consent_confirmed=False)
    )
    assert no_consent.status_code == 422
    assert "WhatsApp" in no_consent.text
    payload = lease_payload(unit["id"])
    del payload["late_fee_agreed"]
    assert client.post("/v1/leases", headers=owner.headers, json=payload).status_code == 422
    assert db_session.scalar(select(Lease.id)) is None


def test_create_lease_records_consent_events_and_deposit_warning(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    unit = ok(client, "POST", "/v1/units", owner.headers, json={"code": "2B"}).json()
    body = ok(
        client,
        "POST",
        "/v1/leases",
        owner.headers,
        json=lease_payload(
            unit["id"],
            deposit_amount="66000.00",
            tenant={"full_name": "José", "phone": "305 555 0102"},
        ),
    ).json()
    codes = {w["code"] for w in body["warnings"]}
    assert codes == {"deposit_above_legal_max", "phone_not_do_area_code"}
    assert "Ley 85-25" in next(
        w["message"] for w in body["warnings"] if w["field"] == "deposit_amount"
    )
    lease_id = body["lease"]["id"]
    assert body["lease"]["late_fee_agreed"] is False

    consent = db_session.scalar(select(ConsentRecord).where(ConsentRecord.lease_id == lease_id))
    assert consent.method == ConsentMethod.CONTRACT_CLAUSE and consent.revoked_at is None
    events = db_session.scalars(
        select(TimelineEvent.event_type).where(TimelineEvent.lease_id == lease_id)
    ).all()
    assert set(events) == {"lease.created", "consent.granted"}

    again = client.post("/v1/leases", headers=owner.headers, json=lease_payload(unit["id"]))
    assert again.status_code == 409 and again.json()["detail"]["code"] == "unit_occupied"


def test_deposit_within_two_months_has_no_warning(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    unit = ok(client, "POST", "/v1/units", owner.headers, json={"code": "3C"}).json()
    body = ok(client, "POST", "/v1/leases", owner.headers, json=lease_payload(unit["id"])).json()
    assert body["warnings"] == []


def test_lease_detail_has_balance_status_punctuality_and_pending_action(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    portfolio = make_portfolio(db_session, owner.org)
    body = ok(client, "GET", f"/v1/leases/{portfolio.lease.id}", owner.headers).json()
    assert body["status"]["state"] == "pending_confirmation"
    assert Decimal(body["balance"]["total"]) == Decimal("25000.00")
    assert len(body["punctuality"]["months"]) == 12
    assert body["pending_action"]["id"] == str(portfolio.inbox_item.id)
    assert body["consent"]["method"] == "contract_clause"
    assert body["tenant"]["full_name"] == "María Fernández"
    assert body["building"]["name"] == "Torre Prueba"


def test_timeline_is_paginated_newest_first(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    lease = make_portfolio(db_session, owner.org).lease
    for _ in range(35):
        make_event(db_session, lease)
    first = ok(client, "GET", f"/v1/leases/{lease.id}", owner.headers).json()["timeline"]
    assert len(first["items"]) == 30 and first["next_cursor"]
    second = ok(
        client,
        "GET",
        f"/v1/leases/{lease.id}/timeline?cursor={first['next_cursor']}",
        owner.headers,
    ).json()
    assert len(second["items"]) == 5 and second["next_cursor"] is None
    ids = [e["id"] for e in first["items"] + second["items"]]
    assert len(set(ids)) == 35
    bad = client.get(f"/v1/leases/{lease.id}/timeline?cursor=zzz", headers=owner.headers)
    assert bad.status_code == 422


def test_update_and_end_lease(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    lease = make_portfolio(db_session, owner.org).lease
    body = ok(
        client,
        "PATCH",
        f"/v1/leases/{lease.id}",
        owner.headers,
        json={"rent_amount": "26000.00", "deposit_amount": "80000.00"},
    ).json()
    assert body["lease"]["rent_amount"] == "26000.00"
    assert [w["code"] for w in body["warnings"]] == ["deposit_above_legal_max"]
    ended = ok(
        client, "POST", f"/v1/leases/{lease.id}/end", owner.headers, json={"end_date": "2026-12-31"}
    ).json()
    assert ended["is_active"] is False
    assert (
        client.post(
            f"/v1/leases/{lease.id}/end", headers=owner.headers, json={"end_date": "2026-12-31"}
        ).status_code
        == 409
    )
    events = db_session.scalars(
        select(TimelineEvent.event_type).where(TimelineEvent.lease_id == lease.id)
    ).all()
    assert {"lease.updated", "lease.ended"} <= set(events)
