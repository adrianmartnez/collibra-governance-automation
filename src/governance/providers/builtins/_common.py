"""Shared validation helpers for built-in source providers."""

from __future__ import annotations

from collections.abc import Mapping

from governance.providers.errors import CODE_INVALID_DESCRIPTOR, ProviderDiagnostic, ProviderError


def reject_unknown_keys(
    config: Mapping[str, object],
    allowed: frozenset[str],
    *,
    pointer_prefix: str = "",
) -> list[ProviderDiagnostic]:
    """Reject top-level keys outside ``allowed`` with JSON pointer paths."""
    diagnostics: list[ProviderDiagnostic] = []
    for key in config:
        if key not in allowed:
            path = f"{pointer_prefix}/{key}" if pointer_prefix else f"/{key}"
            diagnostics.append(
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path=path,
                    message=f"unknown property {key!r} is not allowed",
                )
            )
    return diagnostics


def require_non_empty_string_or_env(
    key: str,
    value: object,
    *,
    pointer: str | None = None,
) -> list[ProviderDiagnostic]:
    path = pointer or f"/{key}"
    if value is None:
        return [
            ProviderDiagnostic(
                code=CODE_INVALID_DESCRIPTOR,
                path=path,
                message=f"{key} is required",
            )
        ]
    if is_env_ref(value):
        return []
    if isinstance(value, str) and value.strip():
        return []
    return [
        ProviderDiagnostic(
            code=CODE_INVALID_DESCRIPTOR,
            path=path,
            message=f"{key} must be a non-empty string or environment reference",
        )
    ]


def is_env_ref(value: object) -> bool:
    from governance.config_contract.env_refs import is_env_ref as _is_env_ref

    return _is_env_ref(value)


def require_env_ref(path: str, value: object) -> None:
    """Require ``value`` to be exactly ``{"$env": NAME}``."""
    if not is_env_ref(value):
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path=path,
                    message="value must be an environment reference",
                )
            ]
        )


def require_literal_relative_path(path_key: str, value: object, *, pointer: str) -> str:
    """Require a literal relative path string; reject ``$env`` and invalid paths."""
    if is_env_ref(value):
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path=pointer,
                    message=(
                        f"{path_key} must be a literal relative path, not an environment reference"
                    ),
                )
            ]
        )
    if not isinstance(value, str):
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path=pointer,
                    message=f"{path_key} must be a string",
                )
            ]
        )
    from governance.config_contract.errors import ConfigSemanticError
    from governance.config_contract.paths import normalize_relative_path

    try:
        return normalize_relative_path(value, pointer=pointer)
    except ConfigSemanticError as exc:
        raise ProviderError(
            [
                ProviderDiagnostic(
                    code=CODE_INVALID_DESCRIPTOR,
                    path=item.path,
                    message=item.message,
                )
                for item in exc.errors
            ]
        ) from exc


def resolve_document_path(context: object, *, pointer: str = "/path") -> object:
    """Resolve provider document path under ``config_root``, or as-is for legacy CLI."""
    from pathlib import Path

    from governance.providers.contracts import ProviderRuntimeContext

    assert isinstance(context, ProviderRuntimeContext)
    raw = context.config.get("path")
    if context.config_root is None:
        if not isinstance(raw, str) or not raw.strip():
            raise ProviderError(
                [
                    ProviderDiagnostic(
                        code=CODE_INVALID_DESCRIPTOR,
                        path=pointer,
                        message="path must be a non-empty string",
                    )
                ]
            )
        return Path(raw)
    rel = require_literal_relative_path("path", raw, pointer=pointer)
    return Path(context.config_root) / Path(*rel.split("/"))
