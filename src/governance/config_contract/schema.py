"""JSON Schema structural validation for governance.yaml."""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from governance.config_contract.errors import (
    CODE_SCHEMA,
    CODE_UNSUPPORTED,
    ConfigSchemaError,
    DiagnosticError,
    UnsupportedConfigVersionError,
)

try:
    from importlib.resources import files
except ImportError:  # pragma: no cover
    from importlib_resources import files  # type: ignore[no-redef]

_SCHEMA_RESOURCES: dict[str, str] = {
    "1": "governance-config.v1.schema.json",
    "2": "governance-config.v2.schema.json",
}
_VALIDATORS: dict[str, Draft202012Validator] = {}


def load_schema(version: str = "1") -> dict[str, Any]:
    """Load a packaged governance-config JSON Schema by expected version."""
    resource = _SCHEMA_RESOURCES.get(version)
    if resource is None:
        raise UnsupportedConfigVersionError(
            [
                DiagnosticError(
                    code=CODE_UNSUPPORTED,
                    path="/schema_version",
                    message="unsupported configuration schema_version",
                )
            ]
        )
    text = (
        files("governance.config_contract.schemas").joinpath(resource).read_text(encoding="utf-8")
    )
    return json.loads(text)


def _get_validator(version: str) -> Draft202012Validator:
    validator = _VALIDATORS.get(version)
    if validator is None:
        validator = Draft202012Validator(load_schema(version))
        _VALIDATORS[version] = validator
    return validator


def _pointer_from_path(path: list[Any]) -> str:
    if not path:
        return ""
    parts: list[str] = []
    for item in path:
        text = str(item).replace("~", "~0").replace("/", "~1")
        parts.append(text)
    return "/" + "/".join(parts)


def _safe_schema_message(error: ValidationError) -> str:
    validator = error.validator
    if validator == "required":
        return "missing required property"
    if validator == "additionalProperties":
        return "unknown property is not allowed"
    if validator == "const":
        return "value is not an allowed constant"
    if validator == "enum":
        return "value is not an allowed enumeration member"
    if validator == "type":
        return "value has an invalid type"
    if validator == "minItems":
        return "array has too few items"
    if validator == "maxItems":
        return "array has too many items"
    if validator == "minLength":
        return "string is empty or too short"
    if validator == "pattern":
        return "string does not match the required pattern"
    return "configuration failed structural validation"


def validate_structure(document: Any, *, version: str = "1") -> None:
    """Validate structural schema for an expected version.

    The ``version`` argument is the loader's expected schema version. A document
    declaring a different ``schema_version`` fails closed as unsupported.
    """
    if version not in _SCHEMA_RESOURCES:
        raise UnsupportedConfigVersionError(
            [
                DiagnosticError(
                    code=CODE_UNSUPPORTED,
                    path="/schema_version",
                    message="unsupported configuration schema_version",
                )
            ]
        )

    if not isinstance(document, dict):
        raise ConfigSchemaError(
            [
                DiagnosticError(
                    code=CODE_SCHEMA,
                    path="",
                    message="configuration root must be a mapping",
                )
            ]
        )

    declared = document.get("schema_version")
    if declared is not None and declared != version:
        raise UnsupportedConfigVersionError(
            [
                DiagnosticError(
                    code=CODE_UNSUPPORTED,
                    path="/schema_version",
                    message="unsupported configuration schema_version",
                )
            ]
        )

    errors: list[DiagnosticError] = []
    for error in sorted(
        _get_validator(version).iter_errors(document),
        key=lambda err: list(err.absolute_path),
    ):
        path = _pointer_from_path(list(error.absolute_path))
        if error.validator == "const" and list(error.absolute_path) == ["schema_version"]:
            raise UnsupportedConfigVersionError(
                [
                    DiagnosticError(
                        code=CODE_UNSUPPORTED,
                        path="/schema_version",
                        message="unsupported configuration schema_version",
                    )
                ]
            )
        errors.append(
            DiagnosticError(
                code=CODE_SCHEMA,
                path=path,
                message=_safe_schema_message(error),
            )
        )
    if errors:
        raise ConfigSchemaError(errors)
