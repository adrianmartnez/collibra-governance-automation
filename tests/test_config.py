"""Configuration loading tests."""

from __future__ import annotations

import pytest

from governance.config import (
    DEFAULT_INVENTORY_OUTPUT_PATH,
    DEFAULT_POSTGRES_SOURCE_NAME,
    Settings,
    load_settings,
)


def test_default_settings() -> None:
    settings = load_settings(dotenv_path=None, environ={})
    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 5432
    assert settings.postgres_db == "governance_demo"
    assert settings.postgres_user == "postgres"
    assert settings.postgres_password == "postgres"
    assert settings.postgres_source_name == DEFAULT_POSTGRES_SOURCE_NAME
    assert settings.inventory_output_path == DEFAULT_INVENTORY_OUTPUT_PATH
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
            "POSTGRES_SOURCE_NAME": "logical-source",
            "INVENTORY_OUTPUT_PATH": "out/inventory.json",
            "LOG_LEVEL": "DEBUG",
        },
    )
    assert settings.postgres_host == "db.local"
    assert settings.postgres_port == 5433
    assert settings.postgres_db == "demo"
    assert settings.postgres_user == "gov"
    assert settings.postgres_password == "secret"
    assert settings.postgres_source_name == "logical-source"
    assert settings.inventory_output_path == "out/inventory.json"
    assert settings.log_level == "DEBUG"


def test_database_url_override() -> None:
    settings = load_settings(
        dotenv_path=None,
        environ={
            "DATABASE_URL": "postgresql://alice:wonder@dbhost:6543/catalog",
            "POSTGRES_SOURCE_NAME": "logical-source",
            "INVENTORY_OUTPUT_PATH": "artifacts/custom.json",
        },
    )
    assert settings.postgres_host == "dbhost"
    assert settings.postgres_port == 6543
    assert settings.postgres_db == "catalog"
    assert settings.postgres_user == "alice"
    assert settings.postgres_password == "wonder"
    assert settings.postgres_source_name == "logical-source"
    assert settings.inventory_output_path == "artifacts/custom.json"


def test_redacted_masks_password() -> None:
    settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="super-secret",
        postgres_source_name="governance-demo",
        inventory_output_path="artifacts/metadata-inventory.json",
    )
    redacted = settings.redacted()
    assert redacted["postgres_password"] == "***"
    assert redacted["postgres_source_name"] == "governance-demo"
    assert redacted["inventory_output_path"] == "artifacts/metadata-inventory.json"
    assert "super-secret" not in str(redacted)


def test_rejects_blank_host() -> None:
    with pytest.raises(ValueError, match="postgres_host"):
        Settings(
            postgres_host=" ",
            postgres_port=5432,
            postgres_db="db",
            postgres_user="user",
            postgres_password="pass",
            postgres_source_name="source",
            inventory_output_path="artifacts/out.json",
        )


def test_rejects_blank_source_name() -> None:
    with pytest.raises(ValueError, match="postgres_source_name"):
        Settings(
            postgres_host="localhost",
            postgres_port=5432,
            postgres_db="db",
            postgres_user="user",
            postgres_password="pass",
            postgres_source_name=" ",
            inventory_output_path="artifacts/out.json",
        )


def test_rejects_blank_inventory_output_path() -> None:
    with pytest.raises(ValueError, match="inventory_output_path"):
        Settings(
            postgres_host="localhost",
            postgres_port=5432,
            postgres_db="db",
            postgres_user="user",
            postgres_password="pass",
            postgres_source_name="source",
            inventory_output_path="",
        )
