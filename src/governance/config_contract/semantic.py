"""Semantic validation for governance.yaml after profile overlay."""

from __future__ import annotations

import re
from typing import Any

from governance.config_contract.errors import (
    CODE_SEMANTIC,
    ConfigSemanticError,
    DiagnosticError,
)
from governance.config_contract.paths import normalize_relative_path

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_semantics(document: dict[str, Any]) -> None:
    errors: list[DiagnosticError] = []

    sources = document.get("sources")
    if not isinstance(sources, list) or len(sources) != 1:
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path="/sources",
                message="exactly one source is required",
            )
        )
    else:
        errors.extend(_validate_source(sources[0], "/sources/0"))

    if "targets" in document:
        targets = document.get("targets")
        if not isinstance(targets, list) or len(targets) != 1:
            errors.append(
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path="/targets",
                    message="when present, targets must contain exactly one target",
                )
            )
        else:
            errors.extend(_validate_target(targets[0], "/targets/0"))

    artifacts = document.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, dict):
            errors.append(
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path="/artifacts",
                    message="artifacts must be a mapping",
                )
            )
        else:
            for key in ("inventory_path", "snapshot_path"):
                if key in artifacts:
                    try:
                        normalize_relative_path(
                            artifacts[key],
                            pointer=f"/artifacts/{key}",
                        )
                    except ConfigSemanticError as exc:
                        errors.extend(exc.errors)

    policies = document.get("policies")
    if policies is not None:
        if not isinstance(policies, dict):
            errors.append(
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path="/policies",
                    message="policies must be a mapping",
                )
            )
        else:
            files = policies.get("files", [])
            if files is None:
                errors.append(
                    DiagnosticError(
                        code=CODE_SEMANTIC,
                        path="/policies/files",
                        message="policies.files must not be null",
                    )
                )
            elif not isinstance(files, list):
                errors.append(
                    DiagnosticError(
                        code=CODE_SEMANTIC,
                        path="/policies/files",
                        message="policies.files must be an array",
                    )
                )
            else:
                for index, item in enumerate(files):
                    try:
                        normalize_relative_path(item, pointer=f"/policies/files/{index}")
                    except ConfigSemanticError as exc:
                        errors.extend(exc.errors)

    if errors:
        raise ConfigSemanticError(errors)


def _validate_source(source: Any, pointer: str) -> list[DiagnosticError]:
    errors: list[DiagnosticError] = []
    if not isinstance(source, dict):
        return [
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=pointer,
                message="source must be a mapping",
            )
        ]
    if source.get("provider") != "postgresql":
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{pointer}/provider",
                message="source provider must be postgresql",
            )
        )
    config = source.get("config")
    if not isinstance(config, dict):
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{pointer}/config",
                message="source config must be a mapping",
            )
        )
        return errors

    source_name = config.get("source_name")
    source_name_env = config.get("source_name_env")
    if source_name is None and source_name_env is None:
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{pointer}/config",
                message="source_name or source_name_env is required",
            )
        )
    if source_name is not None and source_name_env is not None:
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{pointer}/config",
                message="source_name and source_name_env are mutually exclusive",
            )
        )
    if source_name_env is not None:
        errors.extend(_validate_env_name(source_name_env, f"{pointer}/config/source_name_env"))

    connection = config.get("connection")
    if not isinstance(connection, dict):
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{pointer}/config/connection",
                message="connection must be a mapping",
            )
        )
        return errors

    errors.extend(_validate_postgres_connection(connection, f"{pointer}/config/connection"))
    return errors


def _validate_postgres_connection(
    connection: dict[str, Any],
    pointer: str,
) -> list[DiagnosticError]:
    errors: list[DiagnosticError] = []
    url_env = connection.get("database_url_env")
    discrete = ("host_env", "port_env", "db_env", "user_env", "password_env")
    discrete_present = [key for key in discrete if connection.get(key) is not None]

    if url_env is not None and discrete_present:
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=pointer,
                message="database_url_env cannot be combined with discrete connection env refs",
            )
        )
    elif url_env is not None:
        errors.extend(_validate_env_name(url_env, f"{pointer}/database_url_env"))
    elif discrete_present:
        required = ("host_env", "db_env", "user_env", "password_env")
        missing = [key for key in required if connection.get(key) is None]
        if missing:
            errors.append(
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=pointer,
                    message=(
                        "discrete connection refs require host_env, db_env, "
                        "user_env, and password_env"
                    ),
                )
            )
        for key in discrete:
            if connection.get(key) is not None:
                errors.extend(_validate_env_name(connection[key], f"{pointer}/{key}"))
    else:
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=pointer,
                message="connection requires database_url_env or discrete env refs",
            )
        )
    return errors


def _validate_target(target: Any, pointer: str) -> list[DiagnosticError]:
    errors: list[DiagnosticError] = []
    if not isinstance(target, dict):
        return [
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=pointer,
                message="target must be a mapping",
            )
        ]
    if target.get("provider") != "collibra":
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{pointer}/provider",
                message="target provider must be collibra",
            )
        )
    config = target.get("config")
    if not isinstance(config, dict):
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{pointer}/config",
                message="target config must be a mapping",
            )
        )
        return errors

    if config.get("mode_env") is not None:
        errors.extend(_validate_env_name(config["mode_env"], f"{pointer}/config/mode_env"))
    if config.get("execution_mode_env") is not None:
        errors.extend(
            _validate_env_name(
                config["execution_mode_env"],
                f"{pointer}/config/execution_mode_env",
            )
        )

    mapping = config.get("mapping")
    if not isinstance(mapping, dict) or "path" not in mapping:
        errors.append(
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=f"{pointer}/config/mapping",
                message="mapping.path is required",
            )
        )
    else:
        try:
            normalize_relative_path(mapping["path"], pointer=f"{pointer}/config/mapping/path")
        except ConfigSemanticError as exc:
            errors.extend(exc.errors)

    auth = config.get("auth")
    if auth is not None:
        if not isinstance(auth, dict):
            errors.append(
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{pointer}/config/auth",
                    message="auth must be a mapping",
                )
            )
        else:
            for key, value in auth.items():
                if value is not None:
                    errors.extend(_validate_env_name(value, f"{pointer}/config/auth/{key}"))
    return errors


def _validate_env_name(value: Any, pointer: str) -> list[DiagnosticError]:
    if not isinstance(value, str) or not _ENV_NAME_RE.fullmatch(value):
        return [
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=pointer,
                message="invalid environment variable name",
            )
        ]
    return []
