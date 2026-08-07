"""Configuration loading tests."""

from __future__ import annotations

import pytest

from governance.config import Settings, load_settings


def test_default_settings() -> None:
    settings = load_settings(dotenv_path=None, environ={})
    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 5432
    assert settings.postgres_db == "governance_demo"
    assert settings.postgres_user == "postgres"
    assert settings.postgres_password == "postgres"
    assert settings.log_level == "INFO"


def test_env_overrides() -> None:
    settings = load_settings(
        dotenv_path=None,
        environ={
            "POSTGRES_HOST": "db.local",
            "POSTGRES_PORT": "5433",
            "POSTGRES_DB": "demo",
            "POSTGRES_USER": "gov",
            "POSTGRES_PASSWORD": "secret",
            "LOG_LEVEL": "DEBUG",
        },
    )
    assert settings.postgres_host == "db.local"
    assert settings.postgres_port == 5433
    assert settings.postgres_db == "demo"
    assert settings.postgres_user == "gov"
    assert settings.postgres_password == "secret"
    assert settings.log_level == "DEBUG"


def test_database_url_override() -> None:
    settings = load_settings(
        dotenv_path=None,
        environ={
            "DATABASE_URL": "postgresql://alice:wonder@dbhost:6543/catalog",
        },
    )
    assert settings.postgres_host == "dbhost"
    assert settings.postgres_port == 6543
    assert settings.postgres_db == "catalog"
    assert settings.postgres_user == "alice"
    assert settings.postgres_password == "wonder"


def test_redacted_masks_password() -> None:
    settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="super-secret",
    )
    redacted = settings.redacted()
    assert redacted["postgres_password"] == "***"
    assert "super-secret" not in str(redacted)


def test_rejects_blank_host() -> None:
    with pytest.raises(ValueError, match="postgres_host"):
        Settings(
            postgres_host=" ",
            postgres_port=5432,
            postgres_db="db",
            postgres_user="user",
            postgres_password="pass",
        )
