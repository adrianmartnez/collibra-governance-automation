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
    assert settings.collibra_mode == "mock"
    assert settings.collibra_base_url == ""
    assert settings.collibra_timeout_seconds == 10.0


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
    assert redacted["collibra_mode"] == "mock"
    assert redacted["collibra_bearer_token"] == ""
    assert "super-secret" not in str(redacted)


def test_redacted_masks_collibra_secrets() -> None:
    settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="postgres",
        postgres_source_name="governance-demo",
        inventory_output_path="artifacts/metadata-inventory.json",
        collibra_mode="live",
        collibra_base_url="https://collibra.example.invalid",
        collibra_password="collibra-pass",
        collibra_bearer_token="collibra-token",
    )
    # Construction allows both secrets on Settings; live adapter rejects dual auth.
    redacted = settings.redacted()
    assert redacted["collibra_password"] == "***"
    assert redacted["collibra_bearer_token"] == "***"
    assert "collibra-pass" not in str(redacted)
    assert "collibra-token" not in str(redacted)


def test_redacted_masks_oauth_client_secret() -> None:
    settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="postgres",
        postgres_source_name="governance-demo",
        inventory_output_path="artifacts/metadata-inventory.json",
        collibra_mode="live",
        collibra_base_url="https://collibra.example.invalid",
        collibra_client_id="client-id",
        collibra_client_secret="oauth-client-secret",
    )
    redacted = settings.redacted()
    assert redacted["collibra_client_secret"] == "***"
    assert redacted["collibra_client_id"] == "client-id"
    assert "oauth-client-secret" not in str(redacted)


def test_repr_hides_collibra_secret_canaries() -> None:
    settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="postgres",
        postgres_source_name="governance-demo",
        inventory_output_path="artifacts/metadata-inventory.json",
        collibra_mode="live",
        collibra_base_url="https://collibra.example.invalid",
        collibra_username="governance-bot",
        collibra_password="BASIC_PASSWORD_CANARY",
        collibra_bearer_token="BEARER_CANARY",
        collibra_client_id="visible-client-id",
        collibra_client_secret="OAUTH_SECRET_CANARY",
        collibra_token_url="https://idp.example.invalid/oauth/token",
    )
    text = repr(settings)
    assert "BASIC_PASSWORD_CANARY" not in text
    assert "BEARER_CANARY" not in text
    assert "OAUTH_SECRET_CANARY" not in text
    assert "visible-client-id" in text
    assert "https://collibra.example.invalid" in text
    assert "https://idp.example.invalid/oauth/token" in text
    assert "live" in text


def test_repr_and_redacted_omit_token_url_query() -> None:
    canary = "QUERY_SECRET_CANARY"
    settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="postgres",
        postgres_source_name="governance-demo",
        inventory_output_path="artifacts/metadata-inventory.json",
        collibra_mode="live",
        collibra_base_url="https://collibra.example.invalid",
        collibra_client_id="visible-client-id",
        collibra_client_secret="OAUTH_SECRET_CANARY",
        collibra_token_url=f"https://idp.example.invalid/oauth/token?api_key={canary}",
    )
    text = repr(settings)
    redacted = settings.redacted()
    assert canary not in text
    assert canary not in str(redacted)
    assert redacted["collibra_token_url"] == "https://idp.example.invalid/oauth/token"
    assert "https://idp.example.invalid/oauth/token" in text
    assert "?" not in str(redacted["collibra_token_url"])


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


def test_job_poll_settings_defaults() -> None:
    settings = load_settings(dotenv_path=None, environ={})
    assert settings.collibra_job_poll_interval_seconds == 1.0
    assert settings.collibra_job_poll_timeout_seconds == 300.0


def test_job_poll_settings_env_override() -> None:
    settings = load_settings(
        dotenv_path=None,
        environ={
            "COLLIBRA_JOB_POLL_INTERVAL_SECONDS": "2.0",
            "COLLIBRA_JOB_POLL_TIMEOUT_SECONDS": "60",
        },
    )
    assert settings.collibra_job_poll_interval_seconds == 2.0
    assert settings.collibra_job_poll_timeout_seconds == 60.0


@pytest.mark.parametrize(
    ("interval", "timeout", "match"),
    [
        (0.0, 300.0, "collibra_job_poll_interval_seconds"),
        (-1.0, 300.0, "collibra_job_poll_interval_seconds"),
        (1.0, 0.0, "collibra_job_poll_timeout_seconds"),
        (5.0, 2.0, "collibra_job_poll_timeout_seconds must be >="),
    ],
)
def test_rejects_invalid_job_poll_settings(interval: float, timeout: float, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        Settings(
            postgres_host="localhost",
            postgres_port=5432,
            postgres_db="db",
            postgres_user="user",
            postgres_password="pass",
            postgres_source_name="source",
            inventory_output_path="artifacts/out.json",
            collibra_job_poll_interval_seconds=interval,
            collibra_job_poll_timeout_seconds=timeout,
        )
