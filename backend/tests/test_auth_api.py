"""Sign up, sign in, refresh rotation, magic links, cookie sessions + CSRF, sign out, limits."""

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.security import hash_token
from app.models import MagicLinkToken, RefreshToken, User, UserSession
from app.services.auth import PLAIN_COOKIE_NAME
from tests.api_utils import PASSWORD, make_owner

PANEL = "http://localhost:8081"


def register(client: TestClient, client_type: str = "app", **overrides):
    payload = {
        "email": "Carmen@Example.com",
        "password": PASSWORD,
        "full_name": "Carmen RodrÃ­guez",
        "organization_name": "Inversiones RodrÃ­guez",
        "accepted_terms": True,
        "client": client_type,
    }
    payload.update(overrides)
    return client.post("/v1/auth/register", json=payload)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- registration and login --------------------------------------------------------------------


def test_register_app_returns_tokens_and_hashes_the_password(
    client: TestClient, db_session: Session
) -> None:
    response = register(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "carmen@example.com"
    assert body["organization"]["currency"] == "DOP"
    assert body["access_token"] and body["refresh_token"] and body["expires_in"] == 900
    assert body["csrf_token"] is None
    user = db_session.scalar(select(User).where(User.email == "carmen@example.com"))
    assert user.password_hash.startswith("$argon2id$")
    assert user.terms_accepted_at is not None
    assert client.get("/v1/me", headers=bearer(body["access_token"])).status_code == 200


def test_register_requires_terms_and_a_long_password(client: TestClient) -> None:
    assert register(client, accepted_terms=False).status_code == 422
    weak = register(client, password="corta")
    assert weak.status_code == 422
    assert weak.json()["detail"]["code"] == "weak_password"


def test_register_rejects_a_taken_email(client: TestClient) -> None:
    assert register(client).status_code == 201
    again = register(client, email="carmen@example.com")
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "email_taken"


def test_login_ok_and_wrong_password_share_one_message(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session, with_password=True)
    good = client.post(
        "/v1/auth/login", json={"email": owner.user.email, "password": PASSWORD, "client": "app"}
    )
    assert good.status_code == 200
    wrong = client.post(
        "/v1/auth/login",
        json={"email": owner.user.email, "password": "otra-clave-x", "client": "app"},
    )
    unknown = client.post(
        "/v1/auth/login", json={"email": "nadie@example.com", "password": PASSWORD, "client": "app"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_unknown_email_still_runs_a_password_verification(monkeypatch, client: TestClient) -> None:
    calls: list[str | None] = []
    original = security.verify_password

    def spy(password_hash, password):
        calls.append(password_hash)
        return original(password_hash, password)

    monkeypatch.setattr("app.api.v1.auth.verify_password", spy)
    client.post(
        "/v1/auth/login", json={"email": "nadie@example.com", "password": PASSWORD, "client": "app"}
    )
    assert calls == [None]  # verify_password(None, ...) hashes against the dummy hash


def test_login_is_limited_per_email(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session, with_password=True)
    payload = {"email": owner.user.email, "password": "otra-clave-x", "client": "app"}
    for _ in range(5):
        assert client.post("/v1/auth/login", json=payload).status_code == 401
    blocked = client.post("/v1/auth/login", json={**payload, "password": PASSWORD})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "too_many_attempts"
    assert int(blocked.headers["Retry-After"]) > 0


# -- refresh tokens ----------------------------------------------------------------------------


def test_refresh_rotates_and_reuse_revokes_the_family(
    client: TestClient, db_session: Session
) -> None:
    first = register(client).json()
    rotated = client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]

    reused = client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert reused.status_code == 401
    family = db_session.scalars(select(RefreshToken)).all()
    assert family and all(t.revoked_at is not None for t in family)
    # Theft detected: even the newest tokens stop working.
    assert (
        client.post("/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code
        == 401
    )
    assert client.get("/v1/me", headers=bearer(second["access_token"])).status_code == 401


def test_expired_refresh_token_is_rejected(client: TestClient, db_session: Session) -> None:
    body = register(client).json()
    token = db_session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(body["refresh_token"]))
    )
    token.expires_at = token.created_at + dt.timedelta(microseconds=1)
    db_session.flush()
    assert (
        client.post("/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code
        == 401
    )


def test_access_token_tampering_and_expiry(client: TestClient, db_session: Session) -> None:
    body = register(client).json()
    assert client.get("/v1/me", headers=bearer(body["access_token"] + "x")).status_code == 401
    user = db_session.scalar(select(User).where(User.email == "carmen@example.com"))
    family = db_session.scalar(select(RefreshToken.family_id))
    expired, _ = security.create_access_token(
        user_id=user.id,
        organization_id=user.organization_id,
        family_id=family,
        now=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
    )
    assert client.get("/v1/me", headers=bearer(expired)).status_code == 401


# -- magic links -------------------------------------------------------------------------------


def _link_token(outbox) -> str:
    match = re.search(r"token=([A-Za-z0-9_\-]+)", outbox[-1].text)
    assert match
    return match.group(1)


def test_magic_link_is_single_use_and_does_not_reveal_accounts(
    client: TestClient, db_session: Session, outbox
) -> None:
    owner = make_owner(db_session)
    unknown = client.post(
        "/v1/auth/magic-link", json={"email": "nadie@example.com", "client": "app"}
    )
    known = client.post("/v1/auth/magic-link", json={"email": owner.user.email, "client": "app"})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert len(outbox) == 1 and outbox[0].to == owner.user.email
    assert "/auth/magic?token=" in outbox[0].text
    assert "alquilerapp://auth/magic?token=" in outbox[0].text  # development deep link

    token = _link_token(outbox)
    stored = db_session.scalar(select(MagicLinkToken))
    assert stored.token_hash == hash_token(token) and token not in stored.token_hash

    first = client.post("/v1/auth/magic-link/verify", json={"token": token, "client": "app"})
    assert first.status_code == 200 and first.json()["access_token"]
    second = client.post("/v1/auth/magic-link/verify", json={"token": token, "client": "app"})
    assert second.status_code == 401


def test_new_magic_link_invalidates_the_previous_one_and_links_expire(
    client: TestClient, db_session: Session, outbox
) -> None:
    owner = make_owner(db_session)
    client.post("/v1/auth/magic-link", json={"email": owner.user.email, "client": "web"})
    old = _link_token(outbox)
    client.post("/v1/auth/magic-link", json={"email": owner.user.email, "client": "web"})
    new = _link_token(outbox)
    assert (
        client.post("/v1/auth/magic-link/verify", json={"token": old, "client": "web"}).status_code
        == 401
    )

    link = db_session.scalar(
        select(MagicLinkToken).where(MagicLinkToken.token_hash == hash_token(new))
    )
    link.expires_at = link.created_at + dt.timedelta(microseconds=1)
    db_session.flush()
    assert (
        client.post("/v1/auth/magic-link/verify", json={"token": new, "client": "web"}).status_code
        == 401
    )


def test_magic_link_requests_are_limited(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    payload = {"email": owner.user.email, "client": "app"}
    for _ in range(3):
        assert client.post("/v1/auth/magic-link", json=payload).status_code == 202
    assert client.post("/v1/auth/magic-link", json=payload).status_code == 429


# -- web panel: cookie session + CSRF ----------------------------------------------------------


@pytest.fixture
def web(client: TestClient):
    response = register(client, client_type="web")
    assert response.status_code == 201
    return response.json()


def test_web_login_sets_an_httponly_cookie_and_returns_csrf(client: TestClient, web) -> None:
    assert web["access_token"] is None and web["refresh_token"] is None
    assert web["csrf_token"]
    assert PLAIN_COOKIE_NAME in client.cookies
    # (TestClient only keeps the cookie value; the header itself is checked here.)
    session = client.get("/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["csrf_token"] == web["csrf_token"]


def test_set_cookie_flags(client: TestClient) -> None:
    response = register(client, client_type="web")
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header and "path=/" in header
    assert "domain=" not in header


def test_unsafe_requests_with_cookie_need_csrf(client: TestClient, web) -> None:
    payload = {"name": "Nuevo nombre"}
    missing = client.patch("/v1/organization", json=payload)
    assert missing.status_code == 403 and missing.json()["detail"]["code"] == "csrf_failed"
    wrong = client.patch("/v1/organization", json=payload, headers={"X-CSRF-Token": "x" * 64})
    assert wrong.status_code == 403
    good = client.patch(
        "/v1/organization", json=payload, headers={"X-CSRF-Token": web["csrf_token"]}
    )
    assert good.status_code == 200
    # Reads never need the token.
    assert client.get("/v1/organization").status_code == 200


def test_cookie_requests_from_another_origin_are_refused(client: TestClient, web) -> None:
    response = client.patch(
        "/v1/organization",
        json={"name": "X"},
        headers={"X-CSRF-Token": web["csrf_token"], "Origin": "https://malo.example"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "origin_not_allowed"
    allowed = client.patch(
        "/v1/organization",
        json={"name": "X"},
        headers={"X-CSRF-Token": web["csrf_token"], "Origin": PANEL},
    )
    assert allowed.status_code == 200


def test_cookie_and_bearer_together_are_rejected(client: TestClient, web) -> None:
    response = client.get("/v1/me", headers={"Authorization": "Bearer abc"})
    assert response.status_code == 400


def test_idle_and_absolute_session_expiry(client: TestClient, web, db_session: Session) -> None:
    session = db_session.scalar(select(UserSession))
    session.last_seen_at = session.created_at - dt.timedelta(days=8)
    db_session.flush()
    assert client.get("/v1/me").status_code == 401


def test_logout_revokes_the_cookie_session(client: TestClient, web, db_session: Session) -> None:
    response = client.post("/v1/auth/logout", headers={"X-CSRF-Token": web["csrf_token"]})
    assert response.status_code == 204
    assert db_session.scalar(select(UserSession)).revoked_at is not None
    client.cookies.clear()
    assert client.get("/v1/me").status_code == 401


def test_logout_all_ends_every_session_and_access_token(
    client: TestClient, db_session: Session
) -> None:
    app_body = register(client).json()
    login = client.post(
        "/v1/auth/login",
        json={"email": "carmen@example.com", "password": PASSWORD, "client": "web"},
    ).json()
    cookie = client.cookies.get(PLAIN_COOKIE_NAME)
    client.cookies.clear()

    assert (
        client.post("/v1/auth/logout-all", headers=bearer(app_body["access_token"])).status_code
        == 204
    )
    assert client.get("/v1/me", headers=bearer(app_body["access_token"])).status_code == 401
    assert (
        client.post(
            "/v1/auth/refresh", json={"refresh_token": app_body["refresh_token"]}
        ).status_code
        == 401
    )
    client.cookies.set(PLAIN_COOKIE_NAME, cookie)
    assert client.get("/v1/me").status_code == 401
    assert login["csrf_token"]


# -- CORS --------------------------------------------------------------------------------------


def test_cors_only_allows_the_panel_origin(client: TestClient) -> None:
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-csrf-token",
    }
    allowed = client.options("/v1/auth/login", headers={"Origin": PANEL, **preflight})
    assert allowed.headers.get("access-control-allow-origin") == PANEL
    assert allowed.headers.get("access-control-allow-credentials") == "true"
    other = client.options(
        "/v1/auth/login", headers={"Origin": "http://localhost:3000", **preflight}
    )
    assert "access-control-allow-origin" not in other.headers
