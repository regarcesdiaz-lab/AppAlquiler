import pytest

from app.core.config import Settings


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("MESSAGING_MODE", "TIMEZONE", "CORS_ORIGINS", "STORAGE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_safe_defaults(clean_env: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)

    assert settings.MESSAGING_MODE == "simulator"
    assert settings.TIMEZONE == "America/Santo_Domingo"
    assert settings.STORAGE_BACKEND == "local"


def test_cors_origins_from_comma_separated_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CORS_ORIGINS", "http://a.test, http://b.test,")

    settings = Settings(_env_file=None)

    assert settings.CORS_ORIGINS == ["http://a.test", "http://b.test"]


def test_rejects_unknown_messaging_mode(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("MESSAGING_MODE", "production")

    with pytest.raises(ValueError):
        Settings(_env_file=None)
