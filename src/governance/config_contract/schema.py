"""JSON Schema structural validation for governance.yaml."""

from __future__ import annotations

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

_SCHEMA_RESOURCE = "governance-config.v1.schema.json"
_validator: Draft202012Validator | None = None


def load_schema() -> dict[str, Any]:
    text = (
        files("governance.config_contract.schemas")
        .joinpath(_SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
    )
    import json

    return json.loads(text)


def _get_validator() -> Draft202012Validator:
    global _validator
    if _validator is None:
        _validator = Draft202012Validator(load_schema())
    return _validator


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


def validate_structure(document: Any) -> None:
    """Validate structural schema. Raises ConfigSchemaError / UnsupportedConfigVersionError."""
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

    version = document.get("schema_version")
    if version is not None and version != "1":
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
        _get_validator().iter_errors(document),
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
