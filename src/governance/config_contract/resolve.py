"""Resolve CanonicalConfig against environment into runtime Settings."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from governance.config import (
    DEFAULT_COLLIBRA_MODE,
    DEFAULT_COLLIBRA_TIMEOUT_SECONDS,
    DEFAULT_POSTGRES_DB,
    DEFAULT_POSTGRES_HOST,
    DEFAULT_POSTGRES_PASSWORD,
    DEFAULT_POSTGRES_PORT,
    DEFAULT_POSTGRES_SOURCE_NAME,
    DEFAULT_POSTGRES_USER,
    Settings,
    _parse_database_url,
    load_settings,
)
from governance.config_contract.models import CanonicalConfig, SourceConfig, TargetConfig


class ConfigResolutionError(ValueError):
    """Raised when runtime env refs required by YAML cannot be resolved safely."""


def resolve_settings(
    canonical: CanonicalConfig,
    *,
    environ: dict[str, str] | None = None,
    dotenv_path: str | None = ".env",
) -> Settings:
    """Build Settings for GaC mode from effective config + env fallbacks."""
    # Load .env first (override=False) so GaC *_env refs see the same effective
    # environment as legacy load_settings when environ is not injected.
    base = load_settings(dotenv_path=dotenv_path, environ=environ)
    env = dict(os.environ if environ is None else environ)

    source = canonical.sources[0]
    postgres = _resolve_postgres(source, env=env, base=base)

    collibra_mode = base.collibra_mode
    collibra_base_url = base.collibra_base_url
    collibra_username = base.collibra_username
    collibra_password = base.collibra_password
    collibra_bearer_token = base.collibra_bearer_token
    collibra_timeout_seconds = base.collibra_timeout_seconds

    if canonical.targets:
        target = canonical.targets[0]
        resolved = _resolve_collibra(target, env=env, base=base)
        collibra_mode = resolved["mode"]
        collibra_base_url = resolved["base_url"]
        collibra_username = resolved["username"]
        collibra_password = resolved["password"]
        collibra_bearer_token = resolved["bearer_token"]
        collibra_timeout_seconds = resolved["timeout_seconds"]

    inventory_path = str(Path(canonical.config_root) / canonical.artifacts.inventory_path)

    return replace(
        base,
        postgres_host=postgres["host"],
        postgres_port=postgres["port"],
        postgres_db=postgres["db"],
        postgres_user=postgres["user"],
        postgres_password=postgres["password"],
        postgres_source_name=postgres["source_name"],
        inventory_output_path=inventory_path,
        collibra_mode=collibra_mode,
        collibra_base_url=collibra_base_url,
        collibra_username=collibra_username,
        collibra_password=collibra_password,
        collibra_bearer_token=collibra_bearer_token,
        collibra_timeout_seconds=collibra_timeout_seconds,
    )


def resolve_mapping_path(canonical: CanonicalConfig) -> Path | None:
    if not canonical.targets:
        return None
    return Path(canonical.config_root) / canonical.targets[0].config.mapping_path


def resolve_snapshot_path(canonical: CanonicalConfig) -> Path:
    return Path(canonical.config_root) / canonical.artifacts.snapshot_path


def resolve_inventory_path(canonical: CanonicalConfig) -> Path:
    return Path(canonical.config_root) / canonical.artifacts.inventory_path


def _resolve_postgres(
    source: SourceConfig,
    *,
    env: dict[str, str],
    base: Settings,
) -> dict[str, str | int]:
    config = source.config
    if config.source_name is not None:
        source_name = config.source_name
    elif config.source_name_env is not None:
        source_name = env.get(config.source_name_env, DEFAULT_POSTGRES_SOURCE_NAME)
    else:
        source_name = base.postgres_source_name

    connection = config.connection
    if connection.database_url_env is not None:
        url = env.get(connection.database_url_env)
        if not url:
            raise ConfigResolutionError(
                f"environment variable {connection.database_url_env} is required"
            )
        parsed = _parse_database_url(url)
        return {
            "host": str(parsed["postgres_host"]),
            "port": int(parsed["postgres_port"]),
            "db": str(parsed["postgres_db"]),
            "user": str(parsed["postgres_user"]),
            "password": str(parsed["postgres_password"]),
            "source_name": source_name,
        }

    # Discrete refs: DATABASE_URL must not silently override.
    host = _env_or_default(env, connection.host_env, base.postgres_host, DEFAULT_POSTGRES_HOST)
    port_raw = _env_or_default(
        env,
        connection.port_env,
        str(base.postgres_port),
        str(DEFAULT_POSTGRES_PORT),
    )
    db = _env_or_default(env, connection.db_env, base.postgres_db, DEFAULT_POSTGRES_DB)
    user = _env_or_default(env, connection.user_env, base.postgres_user, DEFAULT_POSTGRES_USER)
    password = _env_or_default(
        env,
        connection.password_env,
        base.postgres_password,
        DEFAULT_POSTGRES_PASSWORD,
    )
    return {
        "host": host,
        "port": int(port_raw),
        "db": db,
        "user": user,
        "password": password,
        "source_name": source_name,
    }


def _resolve_collibra(
    target: TargetConfig,
    *,
    env: dict[str, str],
    base: Settings,
) -> dict[str, str | float]:
    config = target.config
    mode = base.collibra_mode
    if config.mode_env is not None:
        mode = env.get(config.mode_env, DEFAULT_COLLIBRA_MODE).strip().lower()

    auth = config.auth
    base_url = base.collibra_base_url
    username = base.collibra_username
    password = base.collibra_password
    bearer = base.collibra_bearer_token
    timeout = base.collibra_timeout_seconds
    if auth is not None:
        if auth.base_url_env is not None:
            base_url = env.get(auth.base_url_env, "")
        if auth.username_env is not None:
            username = env.get(auth.username_env, "")
        if auth.password_env is not None:
            password = env.get(auth.password_env, "")
        if auth.bearer_token_env is not None:
            bearer = env.get(auth.bearer_token_env, "")
        if auth.timeout_seconds_env is not None:
            raw = env.get(auth.timeout_seconds_env)
            timeout = (
                float(raw) if raw is not None and raw.strip() else DEFAULT_COLLIBRA_TIMEOUT_SECONDS
            )

    return {
        "mode": mode,
        "base_url": base_url,
        "username": username,
        "password": password,
        "bearer_token": bearer,
        "timeout_seconds": timeout,
    }


def _env_or_default(
    env: dict[str, str],
    key: str | None,
    base_value: str,
    default: str,
) -> str:
    if key is None:
        return base_value or default
    return env.get(key, base_value or default)
