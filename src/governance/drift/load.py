"""Load drift policy artifacts from disk."""

from __future__ import annotations

from pathlib import Path

import yaml

from governance.drift.errors import (
    CODE_INVALID_POLICY,
    CODE_POLICY_PARSE_ERROR,
    CODE_POLICY_READ_ERROR,
    DiagnosticError,
    DriftError,
)
from governance.drift.policy import parse_and_normalize_policy
from governance.drift.yaml_load import (
    DuplicateKeyError,
    InvalidMappingKeyError,
    load_drift_policy_yaml,
)
from governance.identity.json_values import validate_json_value


def load_drift_policy(path: str | Path):
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_POLICY_PARSE_ERROR,
                    path="/",
                    message="invalid drift policy YAML",
                )
            ]
        ) from exc
    except OSError as exc:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_POLICY_READ_ERROR,
                    path="/",
                    message="unable to read drift policy",
                )
            ]
        ) from exc

    try:
        document = load_drift_policy_yaml(text)
    except DuplicateKeyError as exc:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_POLICY_PARSE_ERROR,
                    path="/",
                    message="duplicate mapping key in drift policy YAML",
                )
            ]
        ) from exc
    except InvalidMappingKeyError as exc:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_POLICY_PARSE_ERROR,
                    path="/",
                    message="invalid drift policy YAML mapping key",
                )
            ]
        ) from exc
    except (ValueError, yaml.YAMLError, RecursionError) as exc:  # type: ignore[name-defined]
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_POLICY_PARSE_ERROR,
                    path="/",
                    message="invalid drift policy YAML",
                )
            ]
        ) from exc

    try:
        _validate_acyclic_policy_document(document)
    except (TypeError, ValueError, RecursionError) as exc:
        raise DriftError(
            [
                DiagnosticError(
                    code=CODE_INVALID_POLICY,
                    path="/",
                    message="invalid drift policy document structure",
                )
            ]
        ) from exc

    return parse_and_normalize_policy(document)


def _validate_acyclic_policy_document(value: object, *, seen: set[int] | None = None) -> None:
    if seen is None:
        seen = set()
    if isinstance(value, dict):
        object_id = id(value)
        if object_id in seen:
            raise ValueError("cyclic structure in drift policy")
        seen.add(object_id)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                _validate_acyclic_policy_document(item, seen=seen)
        finally:
            seen.discard(object_id)
        return
    if isinstance(value, list):
        object_id = id(value)
        if object_id in seen:
            raise ValueError("cyclic structure in drift policy")
        seen.add(object_id)
        try:
            for item in value:
                _validate_acyclic_policy_document(item, seen=seen)
        finally:
            seen.discard(object_id)
        return
    validate_json_value(value)
