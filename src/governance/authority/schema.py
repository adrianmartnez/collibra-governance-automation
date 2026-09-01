"""JSON Schema structural validation for authority files."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from governance.authority.errors import (
    CODE_SCHEMA,
    CODE_UNSUPPORTED,
    AuthorityDiagnosticError,
    AuthoritySchemaError,
    UnsupportedAuthorityVersionError,
)

try:
    from importlib.resources import files
except ImportError:  # pragma: no cover
    from importlib_resources import files  # type: ignore[no-redef]

_SCHEMA_RESOURCE = "governance-authority.v1.schema.json"
_validator: Draft202012Validator | None = None


def load_schema() -> dict[str, Any]:
    import json

    text = (
        files("governance.authority.schemas").joinpath(_SCHEMA_RESOURCE).read_text(encoding="utf-8")
    )
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
    if validator == "minLength":
        return "string is empty or too short"
    return "authority failed structural validation"


def validate_authority_structure(document: Any, *, source: str) -> None:
    if not isinstance(document, dict):
        raise AuthoritySchemaError(
            [
                AuthorityDiagnosticError(
                    code=CODE_SCHEMA,
                    path="",
                    message="authority root must be a mapping",
                    source=source,
                )
            ]
        )

    version = document.get("authority_version")
    if version is not None and version != "1":
        raise UnsupportedAuthorityVersionError(
            [
                AuthorityDiagnosticError(
                    code=CODE_UNSUPPORTED,
                    path="/authority_version",
                    message="unsupported authority_version",
                    source=source,
                )
            ]
        )

    errors: list[AuthorityDiagnosticError] = []
    for error in sorted(
        _get_validator().iter_errors(document),
        key=lambda err: list(err.absolute_path),
    ):
        path = _pointer_from_path(list(error.absolute_path))
        if error.validator == "const" and list(error.absolute_path) == ["authority_version"]:
            raise UnsupportedAuthorityVersionError(
                [
                    AuthorityDiagnosticError(
                        code=CODE_UNSUPPORTED,
                        path="/authority_version",
                        message="unsupported authority_version",
                        source=source,
                    )
                ]
            )
        if error.validator == "const" and list(error.absolute_path) == ["authority_schema"]:
            errors.append(
                AuthorityDiagnosticError(
                    code=CODE_SCHEMA,
                    path="/authority_schema",
                    message="value is not an allowed constant",
                    source=source,
                )
            )
            continue
        errors.append(
            AuthorityDiagnosticError(
                code=CODE_SCHEMA,
                path=path,
                message=_safe_schema_message(error),
                source=source,
            )
        )
    if errors:
        raise AuthoritySchemaError(errors)
