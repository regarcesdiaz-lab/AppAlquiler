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


def test_live_messaging_is_refused_outside_production(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("MESSAGING_MODE", "live")
    for env in ("development", "test"):
        clean_env.setenv("APP_ENV", env)
        with pytest.raises(ValueError, match="only allowed with APP_ENV=production"):
            Settings(_env_file=None)


def test_live_messaging_needs_the_whatsapp_credentials(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("MESSAGING_MODE", "live")
    clean_env.setenv("APP_ENV", "production")
    clean_env.setenv("SECRET_KEY", "x" * 40)
    with pytest.raises(ValueError, match="WHATSAPP_APP_SECRET"):
        Settings(_env_file=None, WHATSAPP_PHONE_NUMBER_ID="1", WHATSAPP_ACCESS_TOKEN="t")
    settings = Settings(
        _env_file=None,
        WHATSAPP_PHONE_NUMBER_ID="1",
        WHATSAPP_ACCESS_TOKEN="t",
        WHATSAPP_VERIFY_TOKEN="v",
        WHATSAPP_APP_SECRET="s",
    )
    assert settings.MESSAGING_MODE == "live" and not settings.simulator_enabled


def test_rejects_unknown_messaging_mode(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("MESSAGING_MODE", "production")

    with pytest.raises(ValueError):
        Settings(_env_file=None)
