"""Core-owned $env reference detection, validation, and resolution."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

from governance.config_contract.errors import (
    CODE_SEMANTIC,
    ConfigSemanticError,
    DiagnosticError,
)
from governance.config_contract.resolution_diagnostics import CODE_ENV_UNRESOLVED
from governance.config_contract.resolve import ConfigResolutionError

ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_KEY = "$env"


def is_env_ref(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value.keys()) == {ENV_KEY}
        and isinstance(value.get(ENV_KEY), str)
    )


def validate_env_ref_shape(value: Mapping[str, object], *, path: str) -> str:
    if set(value.keys()) != {ENV_KEY}:
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=path,
                    message='environment reference must contain exactly the key "$env"',
                )
            ]
        )
    name = value[ENV_KEY]
    if not isinstance(name, str) or ENV_NAME_RE.fullmatch(name) is None:
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=f"{path}/$env",
                    message="environment variable name is invalid",
                )
            ]
        )
    return name


def assert_json_compatible(value: object, *, path: str) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigSemanticError(
                [
                    DiagnosticError(
                        code=CODE_SEMANTIC,
                        path=path,
                        message="numeric values must be finite (NaN/Infinity are not allowed)",
                    )
                ]
            )
        return
    if isinstance(value, Mapping):
        if ENV_KEY in value:
            # Any object that mentions $env must be exactly {"$env": NAME}.
            validate_env_ref_shape(value, path=path)
            return
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigSemanticError(
                    [
                        DiagnosticError(
                            code=CODE_SEMANTIC,
                            path=path,
                            message="object keys must be strings",
                        )
                    ]
                )
            assert_json_compatible(item, path=f"{path}/{key}" if path else f"/{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_json_compatible(item, path=f"{path}/{index}")
        return
    raise ConfigSemanticError(
        [
            DiagnosticError(
                code=CODE_SEMANTIC,
                path=path,
                message="value type is not JSON-compatible for provider config",
            )
        ]
    )


def normalize_provider_config(value: Mapping[str, object]) -> dict[str, object]:
    """Recursively sort object keys; preserve array order and $env refs."""

    def _norm(node: object) -> object:
        if is_env_ref(node):
            assert isinstance(node, Mapping)
            name = validate_env_ref_shape(node, path="/")
            return {ENV_KEY: name}
        if isinstance(node, Mapping):
            items = sorted(((str(k), _norm(v)) for k, v in node.items()), key=lambda kv: kv[0])
            return {k: v for k, v in items}
        if isinstance(node, list):
            return [_norm(item) for item in node]
        return node

    assert_json_compatible(value, path="/config")
    normalized = _norm(value)
    assert isinstance(normalized, dict)
    return normalized


def resolve_env_refs(
    value: object,
    environ: Mapping[str, str],
    *,
    path: str,
) -> object:
    if is_env_ref(value):
        assert isinstance(value, Mapping)
        name = validate_env_ref_shape(value, path=path)
        if name not in environ or environ[name] == "":
            raise ConfigResolutionError(
                f"environment variable {name!r} is unresolved",
                path=path,
                code=CODE_ENV_UNRESOLVED,
            )
        return environ[name]
    if isinstance(value, Mapping):
        resolved: dict[str, object] = {}
        for key in sorted(value.keys()):
            key_s = str(key)
            child_path = f"{path}/{key_s}" if path else f"/{key_s}"
            resolved[key_s] = resolve_env_refs(value[key], environ, path=child_path)
        return resolved
    if isinstance(value, list):
        return [
            resolve_env_refs(item, environ, path=f"{path}/{index}")
            for index, item in enumerate(value)
        ]
    return value
