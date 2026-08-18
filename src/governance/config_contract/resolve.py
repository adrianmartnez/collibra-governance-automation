"""Resolve CanonicalConfig against environment into runtime Settings."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from governance.config import (
    DEFAULT_COLLIBRA_EXECUTION_MODE,
    DEFAULT_COLLIBRA_JOB_POLL_INTERVAL_SECONDS,
    DEFAULT_COLLIBRA_JOB_POLL_TIMEOUT_SECONDS,
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
from governance.config_contract.resolution_diagnostics import (
    CODE_ENV_UNRESOLVED,
    CODE_RUNTIME_INVALID,
)


class ConfigResolutionError(ValueError):
    """Raised when runtime env refs required by YAML cannot be resolved safely."""

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        code: str = CODE_ENV_UNRESOLVED,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.code = code


def resolve_settings(
    canonical: CanonicalConfig,
    *,
    environ: dict[str, str] | None = None,
    dotenv_path: str | None = ".env",
) -> Settings:
    """Build Settings for GaC mode from effective config + env fallbacks."""
    # Load .env first (override=False) so GaC *_env refs see the same effective
    # environment as legacy load_settings when environ is not injected.
    try:
        base = load_settings(dotenv_path=dotenv_path, environ=environ)
    except ValueError as exc:
        # load_settings parses shared process env (DATABASE_URL, timeouts, ports).
        raise ConfigResolutionError(
            _safe_load_settings_message(exc),
            path=_path_for_load_settings_error(exc, canonical),
            code=CODE_RUNTIME_INVALID,
        ) from exc
    env = dict(os.environ if environ is None else environ)

    source = canonical.sources[0]
    postgres = _resolve_postgres(source, env=env, base=base)

    collibra_mode = base.collibra_mode
    collibra_base_url = base.collibra_base_url
    collibra_username = base.collibra_username
    collibra_password = base.collibra_password
    collibra_bearer_token = base.collibra_bearer_token
    collibra_client_id = base.collibra_client_id
    collibra_client_secret = base.collibra_client_secret
    collibra_token_url = base.collibra_token_url
    collibra_oauth_scope = base.collibra_oauth_scope
    collibra_oauth_client_auth = base.collibra_oauth_client_auth
    collibra_timeout_seconds = base.collibra_timeout_seconds
    collibra_job_poll_interval_seconds = base.collibra_job_poll_interval_seconds
    collibra_job_poll_timeout_seconds = base.collibra_job_poll_timeout_seconds
    collibra_execution_mode = base.collibra_execution_mode
    collibra_synchronization_id = base.collibra_synchronization_id
    collibra_batch_max_resources = base.collibra_batch_max_resources
    collibra_batch_max_additional_characteristics = (
        base.collibra_batch_max_additional_characteristics
    )

    if canonical.targets:
        target = canonical.targets[0]
        resolved = _resolve_collibra(target, env=env, base=base)
        collibra_mode = resolved["mode"]
        collibra_base_url = resolved["base_url"]
        collibra_username = resolved["username"]
        collibra_password = resolved["password"]
        collibra_bearer_token = resolved["bearer_token"]
        collibra_client_id = resolved["client_id"]
        collibra_client_secret = resolved["client_secret"]
        collibra_token_url = resolved["token_url"]
        collibra_oauth_scope = resolved["oauth_scope"]
        collibra_oauth_client_auth = resolved["oauth_client_auth"]
        collibra_timeout_seconds = resolved["timeout_seconds"]
        collibra_job_poll_interval_seconds = resolved["job_poll_interval_seconds"]
        collibra_job_poll_timeout_seconds = resolved["job_poll_timeout_seconds"]
        collibra_execution_mode = resolved["execution_mode"]
        collibra_synchronization_id = resolved["synchronization_id"]
        collibra_batch_max_resources = resolved["batch_max_resources"]
        collibra_batch_max_additional_characteristics = resolved[
            "batch_max_additional_characteristics"
        ]

    inventory_path = str(Path(canonical.config_root) / canonical.artifacts.inventory_path)

    try:
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
            collibra_client_id=collibra_client_id,
            collibra_client_secret=collibra_client_secret,
            collibra_token_url=collibra_token_url,
            collibra_oauth_scope=collibra_oauth_scope,
            collibra_oauth_client_auth=collibra_oauth_client_auth,
            collibra_timeout_seconds=collibra_timeout_seconds,
            collibra_job_poll_interval_seconds=collibra_job_poll_interval_seconds,
            collibra_job_poll_timeout_seconds=collibra_job_poll_timeout_seconds,
            collibra_execution_mode=collibra_execution_mode,
            collibra_synchronization_id=collibra_synchronization_id,
            collibra_batch_max_resources=collibra_batch_max_resources,
            collibra_batch_max_additional_characteristics=(
                collibra_batch_max_additional_characteristics
            ),
        )
    except ValueError as exc:
        raise ConfigResolutionError(
            "resolved runtime settings are invalid",
            path=_settings_validation_path(canonical, exc),
            code=CODE_RUNTIME_INVALID,
        ) from exc


def validate_collibra_runtime(
    settings: Settings,
    canonical: CanonicalConfig | None = None,
) -> None:
    """Validate effective Collibra runtime before adapter construction or HTTP.

    Raises ConfigResolutionError for configuration/auth failures (no I/O).
    """
    from governance.integrations.collibra.endpoint import normalize_base_url

    mode = settings.collibra_mode.strip().lower()
    mode_path = _target_mode_path(canonical)
    if mode not in {"mock", "live"}:
        raise ConfigResolutionError(
            "collibra_mode must be 'mock' or 'live'",
            path=mode_path,
            code=CODE_RUNTIME_INVALID,
        )
    if mode != "live":
        return

    base_url_path = _target_auth_path(canonical, "base_url_env")
    try:
        normalize_base_url(settings.collibra_base_url)
    except ValueError as exc:
        raise ConfigResolutionError(
            str(exc),
            path=base_url_path,
            code=CODE_RUNTIME_INVALID,
        ) from exc

    username = (settings.collibra_username or "").strip()
    password = (settings.collibra_password or "").strip()
    bearer = (settings.collibra_bearer_token or "").strip()
    client_id = (settings.collibra_client_id or "").strip()
    client_secret = (settings.collibra_client_secret or "").strip()
    token_url = (settings.collibra_token_url or "").strip()
    oauth_scope = (settings.collibra_oauth_scope or "").strip()
    oauth_client_auth = (settings.collibra_oauth_client_auth or "").strip()
    has_basic_partial = bool(username) or bool(password)
    has_basic = bool(username) and bool(password)
    has_bearer = bool(bearer)
    has_oauth_partial = bool(client_id) or bool(client_secret)
    has_oauth = bool(client_id) and bool(client_secret)

    if sum([has_basic_partial, has_bearer, has_oauth_partial]) > 1:
        raise ConfigResolutionError(
            "live mode accepts exactly one auth method: basic, bearer, or oauth",
            path=_target_auth_path(canonical, "bearer_token_env")
            or _target_auth_path(canonical, "client_id_env")
            or _target_auth_path(canonical, "username_env"),
            code=CODE_RUNTIME_INVALID,
        )
    if has_oauth_partial and not has_oauth:
        path = (
            _target_auth_path(canonical, "client_secret_env")
            if client_id and not client_secret
            else _target_auth_path(canonical, "client_id_env")
        )
        raise ConfigResolutionError(
            "live mode oauth requires both client_id and client_secret",
            path=path,
            code=CODE_RUNTIME_INVALID,
        )
    if has_basic_partial and not has_basic:
        path = (
            _target_auth_path(canonical, "password_env")
            if username and not password
            else _target_auth_path(canonical, "username_env")
        )
        raise ConfigResolutionError(
            "live mode basic auth requires both username and password",
            path=path,
            code=CODE_RUNTIME_INVALID,
        )
    if has_oauth:
        _validate_oauth_runtime(
            canonical,
            token_url=token_url,
            oauth_scope=oauth_scope,
            oauth_client_auth=oauth_client_auth,
            base_url=settings.collibra_base_url,
        )
        return
    if has_basic or has_bearer:
        return
    raise ConfigResolutionError(
        "live mode requires exactly one auth method: basic, bearer, or oauth",
        path=_target_auth_path(canonical, "username_env")
        or _target_auth_path(canonical, "bearer_token_env")
        or _target_auth_path(canonical, "client_id_env")
        or "/targets/0/config/auth",
        code=CODE_RUNTIME_INVALID,
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
                f"environment variable {connection.database_url_env} is required",
                path="/sources/0/config/connection/database_url_env",
                code=CODE_ENV_UNRESOLVED,
            )
        try:
            parsed = _parse_database_url(url)
        except ValueError as exc:
            raise ConfigResolutionError(
                "database URL is invalid",
                path="/sources/0/config/connection/database_url_env",
                code=CODE_RUNTIME_INVALID,
            ) from exc
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
    try:
        port = int(port_raw)
    except (TypeError, ValueError) as exc:
        raise ConfigResolutionError(
            "postgres port must be a positive integer",
            path=(
                "/sources/0/config/connection/port_env"
                if connection.port_env is not None
                else "/sources/0/config/connection"
            ),
            code=CODE_RUNTIME_INVALID,
        ) from exc
    return {
        "host": host,
        "port": port,
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
    client_id = base.collibra_client_id
    client_secret = base.collibra_client_secret
    token_url = base.collibra_token_url
    oauth_scope = base.collibra_oauth_scope
    oauth_client_auth = base.collibra_oauth_client_auth
    timeout = base.collibra_timeout_seconds
    job_poll_interval = base.collibra_job_poll_interval_seconds
    job_poll_timeout = base.collibra_job_poll_timeout_seconds
    execution_mode = base.collibra_execution_mode
    synchronization_id = base.collibra_synchronization_id
    if config.execution_mode_env is not None:
        execution_mode = env.get(config.execution_mode_env, DEFAULT_COLLIBRA_EXECUTION_MODE)
    if config.synchronization_id is not None:
        synchronization_id = config.synchronization_id
    elif config.synchronization_id_env is not None:
        synchronization_id = env.get(config.synchronization_id_env, "")
    batch_max_resources = base.collibra_batch_max_resources
    batch_max_additional = base.collibra_batch_max_additional_characteristics
    if config.batch_max_resources_env is not None:
        raw_resources = env.get(config.batch_max_resources_env)
        if raw_resources is not None and raw_resources.strip():
            try:
                batch_max_resources = int(raw_resources)
            except ValueError as exc:
                raise ConfigResolutionError(
                    "collibra batch_max_resources must be a positive integer",
                    path="/targets/0/config/batch_max_resources_env",
                    code=CODE_RUNTIME_INVALID,
                ) from exc
    if config.batch_max_additional_characteristics_env is not None:
        raw_additional = env.get(config.batch_max_additional_characteristics_env)
        if raw_additional is not None and raw_additional.strip():
            try:
                batch_max_additional = int(raw_additional)
            except ValueError as exc:
                raise ConfigResolutionError(
                    "collibra batch_max_additional_characteristics must be a positive integer",
                    path="/targets/0/config/batch_max_additional_characteristics_env",
                    code=CODE_RUNTIME_INVALID,
                ) from exc
    if auth is not None:
        if auth.base_url_env is not None:
            base_url = env.get(auth.base_url_env, "")
        if auth.username_env is not None:
            username = env.get(auth.username_env, "")
        if auth.password_env is not None:
            password = env.get(auth.password_env, "")
        if auth.bearer_token_env is not None:
            bearer = env.get(auth.bearer_token_env, "")
        if auth.client_id_env is not None:
            client_id = env.get(auth.client_id_env, "")
        if auth.client_secret_env is not None:
            client_secret = env.get(auth.client_secret_env, "")
        if auth.token_url_env is not None:
            token_url = env.get(auth.token_url_env, "")
        if auth.scope_env is not None:
            oauth_scope = env.get(auth.scope_env, "")
        if auth.oauth_client_auth_env is not None:
            oauth_client_auth = env.get(auth.oauth_client_auth_env, "")
        if auth.timeout_seconds_env is not None:
            raw = env.get(auth.timeout_seconds_env)
            if raw is not None and raw.strip():
                try:
                    timeout = float(raw)
                except ValueError as exc:
                    raise ConfigResolutionError(
                        "collibra timeout_seconds must be a positive number",
                        path="/targets/0/config/auth/timeout_seconds_env",
                        code=CODE_RUNTIME_INVALID,
                    ) from exc
            else:
                timeout = DEFAULT_COLLIBRA_TIMEOUT_SECONDS
        if auth.job_poll_interval_seconds_env is not None:
            raw = env.get(auth.job_poll_interval_seconds_env)
            if raw is not None and raw.strip():
                try:
                    job_poll_interval = float(raw)
                except ValueError as exc:
                    raise ConfigResolutionError(
                        "collibra job_poll_interval_seconds must be a positive number",
                        path="/targets/0/config/auth/job_poll_interval_seconds_env",
                        code=CODE_RUNTIME_INVALID,
                    ) from exc
            else:
                job_poll_interval = DEFAULT_COLLIBRA_JOB_POLL_INTERVAL_SECONDS
        if auth.job_poll_timeout_seconds_env is not None:
            raw = env.get(auth.job_poll_timeout_seconds_env)
            if raw is not None and raw.strip():
                try:
                    job_poll_timeout = float(raw)
                except ValueError as exc:
                    raise ConfigResolutionError(
                        "collibra job_poll_timeout_seconds must be a positive number",
                        path="/targets/0/config/auth/job_poll_timeout_seconds_env",
                        code=CODE_RUNTIME_INVALID,
                    ) from exc
            else:
                job_poll_timeout = DEFAULT_COLLIBRA_JOB_POLL_TIMEOUT_SECONDS

    return {
        "mode": mode,
        "base_url": base_url,
        "username": username,
        "password": password,
        "bearer_token": bearer,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_url": token_url,
        "oauth_scope": oauth_scope,
        "oauth_client_auth": oauth_client_auth,
        "timeout_seconds": timeout,
        "job_poll_interval_seconds": job_poll_interval,
        "job_poll_timeout_seconds": job_poll_timeout,
        "execution_mode": execution_mode,
        "synchronization_id": synchronization_id,
        "batch_max_resources": batch_max_resources,
        "batch_max_additional_characteristics": batch_max_additional,
    }


def _validate_oauth_runtime(
    canonical: CanonicalConfig | None,
    *,
    token_url: str,
    oauth_scope: str,
    oauth_client_auth: str,
    base_url: str,
) -> None:
    from governance.integrations.collibra.auth import parse_oauth_client_auth
    from governance.integrations.collibra.endpoint import (
        classify_transport,
        normalize_base_url,
        normalize_token_url,
    )

    auth = None
    if canonical is not None and canonical.targets:
        auth = canonical.targets[0].config.auth
    native_idp_fields_declared = bool(
        auth is not None
        and (auth.token_url_env or auth.scope_env or auth.oauth_client_auth_env)
        and not token_url
    )
    if not token_url:
        if oauth_scope or oauth_client_auth or native_idp_fields_declared:
            raise ConfigResolutionError(
                "native oauth rejects token_url, scope, and oauth_client_auth",
                path=_target_auth_path(canonical, "token_url_env")
                or _target_auth_path(canonical, "scope_env")
                or _target_auth_path(canonical, "oauth_client_auth_env"),
                code=CODE_RUNTIME_INVALID,
            )
        try:
            classified = classify_transport(normalize_base_url(base_url))
        except ValueError:
            raise ConfigResolutionError(
                "oauth token endpoint is invalid",
                path=_target_auth_path(canonical, "base_url_env"),
                code=CODE_RUNTIME_INVALID,
            ) from None
        if classified == "remote_http":
            raise ConfigResolutionError(
                "oauth token endpoint must use HTTPS or HTTP loopback",
                path=_target_auth_path(canonical, "base_url_env"),
                code=CODE_RUNTIME_INVALID,
            )
        return

    try:
        normalized_token_url = normalize_token_url(token_url)
        classified = classify_transport(normalized_token_url)
        if oauth_client_auth:
            parse_oauth_client_auth(oauth_client_auth)
    except ValueError as exc:
        message = str(exc)
        if "embed" in message.lower():
            stable = "oauth token_url must not embed credentials"
        elif "query string" in message.lower():
            stable = "oauth token_url must not include a query string"
        elif "https or http loopback" in message.lower():
            stable = "oauth token endpoint must use HTTPS or HTTP loopback"
        elif "oauth_client_auth" in message.lower():
            stable = "oauth_client_auth must be client_secret_post or client_secret_basic"
        else:
            stable = "oauth token endpoint is invalid"
        lowered = message.lower()
        if "client_id" in lowered or "client_secret" in lowered or token_url.lower() in lowered:
            stable = "oauth token_url must not embed credentials"
        raise ConfigResolutionError(
            stable,
            path=_target_auth_path(canonical, "token_url_env"),
            code=CODE_RUNTIME_INVALID,
        ) from None
    if classified == "remote_http":
        raise ConfigResolutionError(
            "oauth token endpoint must use HTTPS or HTTP loopback",
            path=_target_auth_path(canonical, "token_url_env"),
            code=CODE_RUNTIME_INVALID,
        )


def _env_or_default(
    env: dict[str, str],
    key: str | None,
    base_value: str,
    default: str,
) -> str:
    if key is None:
        return base_value or default
    return env.get(key, base_value or default)


def _target_mode_path(canonical: CanonicalConfig | None) -> str:
    if canonical is None or not canonical.targets:
        return "/targets/0/config/mode_env"
    mode_env = canonical.targets[0].config.mode_env
    if mode_env is not None:
        return "/targets/0/config/mode_env"
    return "/targets/0/config"


def _target_auth_path(canonical: CanonicalConfig | None, field: str) -> str:
    if canonical is None or not canonical.targets:
        return f"/targets/0/config/auth/{field}"
    auth = canonical.targets[0].config.auth
    if auth is None:
        return "/targets/0/config/auth"
    if getattr(auth, field, None) is not None:
        return f"/targets/0/config/auth/{field}"
    return "/targets/0/config/auth"


def _safe_load_settings_message(exc: BaseException) -> str:
    text = str(exc).strip()
    lowered = text.lower()
    if "database_url" in lowered or "postgresql://" in lowered or "postgres://" in lowered:
        return "database URL is invalid"
    if "could not convert string to float" in lowered or "timeout" in lowered:
        return "collibra timeout_seconds must be a positive number"
    if "invalid literal for int" in lowered or "port" in lowered:
        return "postgres port must be a positive integer"
    if not text:
        return "resolved runtime settings are invalid"
    for marker in (
        "password",
        "bearer",
        "authorization",
        "token=",
        "client_secret",
        "access_token",
    ):
        if marker in lowered:
            return "resolved runtime settings are invalid"
    return text


def _path_for_load_settings_error(exc: BaseException, canonical: CanonicalConfig) -> str:
    text = str(exc).lower()
    if "database_url" in text or "postgresql://" in text or "postgres://" in text:
        connection = canonical.sources[0].config.connection
        if connection.database_url_env is not None:
            return "/sources/0/config/connection/database_url_env"
        return "/sources/0/config/connection"
    if "could not convert string to float" in text or "timeout" in text:
        return _target_auth_path(canonical, "timeout_seconds_env")
    if "invalid literal for int" in text:
        connection = canonical.sources[0].config.connection
        if connection.port_env is not None:
            return "/sources/0/config/connection/port_env"
        return "/sources/0/config/connection"
    return _settings_validation_path(canonical, exc)


def _settings_validation_path(canonical: CanonicalConfig, exc: BaseException) -> str:
    text = str(exc).lower()
    if "collibra_mode" in text:
        return _target_mode_path(canonical)
    if "collibra_execution_mode" in text:
        if canonical.targets and canonical.targets[0].config.execution_mode_env is not None:
            return "/targets/0/config/execution_mode_env"
        return "/targets/0/config"
    if "collibra_synchronization_id" in text:
        if canonical.targets and canonical.targets[0].config.synchronization_id_env is not None:
            return "/targets/0/config/synchronization_id_env"
        if canonical.targets and canonical.targets[0].config.synchronization_id is not None:
            return "/targets/0/config/synchronization_id"
        return "/targets/0/config"
    if "batch ceilings" in text or "batch_max" in text:
        if canonical.targets and canonical.targets[0].config.batch_max_resources_env is not None:
            return "/targets/0/config/batch_max_resources_env"
        if (
            canonical.targets
            and canonical.targets[0].config.batch_max_additional_characteristics_env is not None
        ):
            return "/targets/0/config/batch_max_additional_characteristics_env"
        return "/targets/0/config"
    if "collibra_timeout" in text:
        return _target_auth_path(canonical, "timeout_seconds_env")
    if "postgres_port" in text:
        connection = canonical.sources[0].config.connection
        if connection.port_env is not None:
            return "/sources/0/config/connection/port_env"
        if connection.database_url_env is not None:
            return "/sources/0/config/connection/database_url_env"
        return "/sources/0/config/connection"
    if "postgres_" in text:
        return "/sources/0/config/connection"
    return "/"
