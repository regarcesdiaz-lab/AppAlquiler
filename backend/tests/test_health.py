from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.api import health
from app.core.config import Settings
from app.core.db import get_db


def test_health_reports_ok_when_services_are_up(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "redis": "ok",
        "version": __version__,
    }


def test_health_reports_redis_error(client: TestClient, monkeypatch) -> None:
    unreachable = Settings(_env_file=None, REDIS_URL="redis://127.0.0.1:1/0")
    monkeypatch.setattr(health, "get_settings", lambda: unreachable)

    body = client.get("/health").json()

    assert body["redis"] == "error"
    assert body["database"] == "ok"


class _BrokenSession:
    def execute(self, *args, **kwargs):
        raise RuntimeError("database is down")


def test_health_reports_database_error(app, client: TestClient) -> None:
    app.dependency_overrides[get_db] = lambda: _BrokenSession()

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["database"] == "error"


def test_db_session_is_isolated_per_test(db_session: Session) -> None:
    assert db_session.execute(text("SELECT current_database()")).scalar() == "alquilerapp_test"
