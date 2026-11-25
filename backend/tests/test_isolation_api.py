"""Multi-tenant isolation (CLAUDE.md rule 8) on every endpoint.

Organization A is signed in; organization B has a full portfolio. Every endpoint that takes an
id must answer 404 for B's rows, lists must not show them, and A's writes on its own
"singleton" resources (settings, organization) must leave B untouched.

The guard test walks every route the app really serves and fails when one is neither in the
matrix below nor declared public, so no endpoint skips this check.
"""

import re

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import AssistantProfile, DeviceToken, Organization
from app.models.enums import AssistantTone, DevicePlatform
from tests.api_utils import Owner, Portfolio, make_owner, make_portfolio
from tests.factories import add
from tests.route_inventory import app_routes

B_DEVICE = "ExponentPushToken[orgB]"

# Routes that take an id of another organization's row → 404. "{x}" placeholders are filled
# from organization B's portfolio.
ID_ROUTES: list[tuple[str, str, dict | None]] = [
    ("GET", "/v1/buildings/{building}", None),
    ("PATCH", "/v1/buildings/{building}", {"name": "Hackeado"}),
    ("DELETE", "/v1/buildings/{building}", None),
    ("GET", "/v1/units/{unit}", None),
    ("PATCH", "/v1/units/{unit}", {"code": "X"}),
    ("DELETE", "/v1/units/{vacant_unit}", None),
    ("GET", "/v1/tenants/{tenant}", None),
    ("PATCH", "/v1/tenants/{tenant}", {"full_name": "Hackeado"}),
    ("DELETE", "/v1/tenants/{tenant}", None),
    ("GET", "/v1/leases/{lease}", None),
    ("GET", "/v1/leases/{lease}/timeline", None),
    ("PATCH", "/v1/leases/{lease}", {"rent_amount": "1.00"}),
    ("POST", "/v1/leases/{lease}/end", {"end_date": "2026-12-31"}),
    ("POST", "/v1/leases/{lease}/payments/cash", {"amount": "1.00", "paid_on": "2026-01-01"}),
    ("POST", "/v1/leases/{lease}/reminders", None),
    ("POST", "/v1/payments/{payment}/confirm", None),
    ("POST", "/v1/payments/{payment}/reject", {"reason": "x"}),
    ("POST", "/v1/inbox/{inbox_item}/resolve", {"status": "resolved"}),
    ("PATCH", "/v1/settings/bank-accounts/{bank_account}", {"alias": "x"}),
    ("DELETE", "/v1/settings/bank-accounts/{bank_account}", None),
    ("DELETE", "/v1/devices/{device}", None),
]

# Creating something that points at B's rows → 404 (never created).
CROSS_CREATE: list[tuple[str, str, dict]] = [
    ("POST", "/v1/units", {"building_id": "{building}", "code": "Z9"}),
    (
        "POST",
        "/v1/leases",
        {
            "unit_id": "{vacant_unit}",
            "tenant": {"full_name": "X", "phone": "8095559999"},
            "rent_amount": "1000.00",
            "due_day": 1,
            "late_fee_agreed": False,
            "start_date": "2026-01-01",
            "consent_confirmed": True,
        },
    ),
]

# Lists: B's ids must not appear for A.
LIST_ROUTES = [
    "/v1/buildings",
    "/v1/units",
    "/v1/tenants",
    "/v1/leases",
    "/v1/payments",
    "/v1/home",
    "/v1/settings/bank-accounts?include_inactive=true",
]

# Scoped to the signed-in organization by construction (no id in the path).
SELF_ROUTES = {
    ("GET", "/v1/me"),
    ("PATCH", "/v1/me"),
    ("POST", "/v1/me/password"),
    ("DELETE", "/v1/me/account"),
    ("GET", "/v1/organization"),
    ("PATCH", "/v1/organization"),
    ("POST", "/v1/devices"),
    ("GET", "/v1/onboarding"),
    ("GET", "/v1/settings/rule-set"),
    ("PUT", "/v1/settings/rule-set"),
    ("GET", "/v1/settings/assistant"),
    ("PUT", "/v1/settings/assistant"),
    ("POST", "/v1/settings/bank-accounts"),
    ("POST", "/v1/buildings"),
    ("POST", "/v1/tenants"),
    ("GET", "/v1/imports/template.xlsx"),
    ("POST", "/v1/imports"),
    ("GET", "/v1/payments/export.xlsx"),
}

# Not scoped to an organization on purpose. Each group says why.
PUBLIC_PREFIXES = (
    "/v1/auth/",  # sign-in, refresh, magic links, logout: the session itself
)
PUBLIC_ROUTES = {
    ("GET", "/health"),
    # Interactive docs and schema (turned off with APP_ENV=production).
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
    # Meta's WhatsApp webhook: subscription token (GET) and HMAC signature (POST), no session.
    ("GET", "/webhooks/whatsapp"),
    ("POST", "/webhooks/whatsapp"),
    # Local WhatsApp simulator: only mounted outside production with MESSAGING_MODE=simulator
    # (tests/test_dev_simulator.py checks it is absent in production).
    ("GET", "/dev/whatsapp"),
    ("GET", "/dev/whatsapp/api/contacts"),
    ("GET", "/dev/whatsapp/api/chats/{phone}"),
    ("DELETE", "/dev/whatsapp/api/chats/{phone}"),
    ("POST", "/dev/whatsapp/api/chats/{phone}/fail-last"),
    ("POST", "/dev/whatsapp/api/messages"),
    ("POST", "/dev/whatsapp/api/media"),
}


@pytest.fixture
def orgs(db_session: Session) -> tuple[Owner, Owner, Portfolio, Portfolio]:
    a = make_owner(db_session)
    b = make_owner(db_session)
    pa = make_portfolio(db_session, a.org, phone="+18095550101")
    pb = make_portfolio(db_session, b.org, phone="+18095550101")
    add(
        db_session,
        DeviceToken(
            organization_id=b.org.id,
            user_id=b.user.id,
            expo_push_token=B_DEVICE,
            platform=DevicePlatform.ANDROID,
        ),
    )
    add(
        db_session,
        AssistantProfile(organization_id=b.org.id, display_name="Bot B", tone=AssistantTone.FORMAL),
    )
    return a, b, pa, pb


def _ids(p: Portfolio) -> dict[str, str]:
    return {
        "building": str(p.building.id),
        "unit": str(p.unit.id),
        "vacant_unit": str(p.vacant_unit.id),
        "tenant": str(p.tenant.id),
        "lease": str(p.lease.id),
        "payment": str(p.payment.id),
        "inbox_item": str(p.inbox_item.id),
        "bank_account": str(p.bank_account.id),
        "device": B_DEVICE,
    }


def _fill(value, ids: dict[str, str]):
    if isinstance(value, str):
        return value.format(**ids) if "{" in value else value
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    return value


@pytest.mark.parametrize(("method", "path", "body"), ID_ROUTES, ids=lambda x: str(x))
def test_other_organizations_rows_are_404(
    client: TestClient, orgs, method: str, path: str, body: dict | None
) -> None:
    a, _, _, pb = orgs
    response = client.request(method, _fill(path, _ids(pb)), headers=a.headers, json=body)
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "path", "body"), CROSS_CREATE, ids=lambda x: str(x))
def test_cannot_create_rows_pointing_at_another_organization(
    client: TestClient, orgs, method: str, path: str, body: dict
) -> None:
    a, _, _, pb = orgs
    response = client.request(method, path, headers=a.headers, json=_fill(body, _ids(pb)))
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("path", LIST_ROUTES)
def test_lists_only_show_the_own_organization(client: TestClient, orgs, path: str) -> None:
    a, _, pa, pb = orgs
    response = client.get(path, headers=a.headers)
    assert response.status_code == 200, response.text
    for value in _ids(pb).values():
        assert value not in response.text
    own = [str(pa.building.id), str(pa.unit.id), str(pa.tenant.id), str(pa.lease.id)]
    assert any(v in response.text for v in own) or path.startswith("/v1/settings")


def test_export_only_contains_the_own_organization(client: TestClient, orgs, db_session) -> None:
    a, _, _, pb = orgs
    pb.tenant.full_name = "Inquilino de B"
    db_session.flush()
    response = client.get("/v1/payments/export.xlsx", headers=a.headers)
    assert response.status_code == 200
    from io import BytesIO

    from openpyxl import load_workbook

    sheet = load_workbook(BytesIO(response.content)).active
    names = {row[1] for row in sheet.iter_rows(min_row=2, values_only=True)}
    assert names == {"María Fernández"}


def test_singleton_writes_leave_the_other_organization_untouched(
    client: TestClient, orgs, db_session: Session
) -> None:
    a, b, _, _ = orgs
    b_name = b.org.name
    for method, path, body in [
        ("PATCH", "/v1/organization", {"name": "Solo A"}),
        ("PUT", "/v1/settings/assistant", {"display_name": "Bot A", "tone": "cordial"}),
        ("PUT", "/v1/settings/rule-set", {"template": "strict"}),
        ("PATCH", "/v1/me", {"full_name": "Dueña A"}),
    ]:
        assert client.request(method, path, headers=a.headers, json=body).status_code == 200
    db_session.expire_all()
    assert db_session.get(Organization, b.org.id).name == b_name
    assistant_b = db_session.query(AssistantProfile).filter_by(organization_id=b.org.id).one()
    assert assistant_b.display_name == "Bot B"
    assert client.get("/v1/settings/rule-set", headers=b.headers).json()["template"] == "standard"
    assert client.get("/v1/me", headers=b.headers).json()["full_name"] != "Dueña A"
    # Same tenant phone in both organizations is allowed (unique per organization only).
    assert client.get("/v1/home", headers=b.headers).json()["counts"]["pending_confirmation"] == 1


def _template(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.split("?")[0])


def _covered() -> set[tuple[str, str]]:
    covered = {(m, _template(p)) for m, p, _ in ID_ROUTES}
    covered |= {(m, _template(p)) for m, p, _ in CROSS_CREATE}
    covered |= {("GET", _template(p)) for p in LIST_ROUTES}
    covered |= {(m, _template(p)) for m, p in SELF_ROUTES}
    return covered


def _is_public(method: str, path: str) -> bool:
    return path.startswith(PUBLIC_PREFIXES) or (method, _template(path)) in {
        (m, _template(p)) for m, p in PUBLIC_ROUTES
    }


def uncovered_routes(app: FastAPI) -> list[str]:
    """Routes of the app that are neither in the isolation matrix nor declared public."""
    covered = _covered()
    return sorted(
        f"{method} {path}"
        for method, path in app_routes(app)
        if not _is_public(method, path) and (method, _template(path)) not in covered
    )


def test_every_route_is_covered_by_the_isolation_tests() -> None:
    missing = uncovered_routes(create_app())
    assert missing == [], f"Add these routes to tests/test_isolation_api.py: {missing}"


def test_the_route_inventory_sees_the_real_routes() -> None:
    # Guards the guard: an empty inventory would make the test above pass without checking.
    routes = app_routes(create_app())
    assert len(routes) > 60
    for expected in [
        ("GET", "/v1/me"),
        ("POST", "/v1/leases/{lease_id}/reminders"),
        ("POST", "/webhooks/whatsapp"),
        ("POST", "/dev/whatsapp/api/messages"),
        ("GET", "/health"),
    ]:
        assert expected in routes


def test_every_listed_route_still_exists() -> None:
    # A route renamed or removed must leave the matrix too, or it would cover nothing.
    real = {(m, _template(p)) for m, p in app_routes(create_app())}
    listed = _covered() | {(m, _template(p)) for m, p in PUBLIC_ROUTES}
    assert sorted(listed - real) == []


def test_the_guard_fails_when_a_route_is_missing() -> None:
    app = create_app()
    extra = APIRouter(prefix="/v1")

    @extra.get("/fake/{fake_id}/secrets")
    def fake_secrets(fake_id: str) -> dict:  # pragma: no cover - never called
        return {}

    @extra.post("/fake")
    def fake_create() -> dict:  # pragma: no cover - never called
        return {}

    app.include_router(extra)
    app.add_api_route("/unlisted", fake_create, methods=["DELETE"])
    assert uncovered_routes(app) == [
        "DELETE /unlisted",
        "GET /v1/fake/{fake_id}/secrets",
        "POST /v1/fake",
    ]
