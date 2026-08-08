"""Pinned ODCS v3.1.0 schema loading and structural validation.

Official media type (documentation only): application/odcs+yaml;version=3.1.0
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import FormatChecker
from jsonschema.exceptions import ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for

from governance.integrations.odcs.errors import (
    CODE_SCHEMA,
    CODE_UNSUPPORTED_VERSION,
    OdcsDiagnostic,
    OdcsSchemaError,
    OdcsUnsupportedVersionError,
)

try:
    from importlib.resources import files
except ImportError:  # pragma: no cover
    from importlib_resources import files  # type: ignore[no-redef]

SUPPORTED_API_VERSION = "v3.1.0"
_SCHEMA_RESOURCE = "odcs-json-schema-v3.1.0.json"
# Integrity digest of the upstream v3.1.0 schema artifact (tests only; not graph identity).
ODCS_SCHEMA_SHA256 = "2cb7dd6fe43344d2233e0406438622681dc3ebadcf8f0d606a15b40c8f6752c0"
_MAX_JSON_TREE_DEPTH = 64

_REQUIRED_PROPERTY_RE = re.compile(r"^'((?:\\'|[^'])*)' is a required property")
_QUOTED_NAME_RE = re.compile(r"'((?:\\'|[^'])*)'")

_validator: Validator | None = None


def load_odcs_schema() -> dict[str, Any]:
    """Load the pinned ODCS JSON Schema resource (offline)."""
    text = (
        files("governance.integrations.odcs.schemas")
        .joinpath(_SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def _get_validator() -> Validator:
    global _validator
    if _validator is None:
        schema = load_odcs_schema()
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        _validator = validator_cls(schema, format_checker=FormatChecker())
    return _validator


def _pointer_from_path(path: Sequence[Any]) -> str:
    if not path:
        return ""
    parts: list[str] = []
    for item in path:
        text = str(item).replace("~", "~0").replace("/", "~1")
        parts.append(text)
    return "/" + "/".join(parts)


def _join_pointer(parent: str, child: str) -> str:
    escaped = child.replace("~", "~0").replace("/", "~1")
    if not parent:
        return "/" + escaped
    return parent + "/" + escaped


def _safe_schema_message(validator: str | None) -> str:
    if validator == "required":
        return "missing required property"
    if validator in {"additionalProperties", "unevaluatedProperties"}:
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
    if validator == "uniqueItems":
        return "array items must be unique"
    if validator == "format":
        return "value does not match the required format"
    return "ODCS document failed structural validation"


def _diagnostics_from_error(error: ValidationError) -> list[OdcsDiagnostic]:
    base_path = _pointer_from_path(list(error.absolute_path))
    validator = error.validator

    if validator == "required":
        match = _REQUIRED_PROPERTY_RE.match(error.message)
        if match:
            prop = match.group(1).replace("\\'", "'")
            return [
                OdcsDiagnostic(
                    code=CODE_SCHEMA,
                    path=_join_pointer(base_path, prop),
                    message=_safe_schema_message("required"),
                )
            ]

    if (
        validator in {"additionalProperties", "unevaluatedProperties"}
        and "unexpected" in error.message
    ):
        names = [name.replace("\\'", "'") for name in _QUOTED_NAME_RE.findall(error.message)]
        if names:
            return [
                OdcsDiagnostic(
                    code=CODE_SCHEMA,
                    path=_join_pointer(base_path, name),
                    message=_safe_schema_message(validator),
                )
                for name in names
            ]

    return [
        OdcsDiagnostic(
            code=CODE_SCHEMA,
            path=base_path,
            message=_safe_schema_message(validator),
        )
    ]


def copy_json_tree(value: object, *, _depth: int = 0, _stack: frozenset[int] | None = None) -> Any:
    """Deep-copy a JSON-compatible tree; reject cycles, non-finite floats, and excess depth."""
    if _stack is None:
        _stack = frozenset()
    if _depth > _MAX_JSON_TREE_DEPTH:
        raise OdcsSchemaError(
            [
                OdcsDiagnostic(
                    code=CODE_SCHEMA,
                    path="",
                    message="document is not a finite JSON tree",
                )
            ]
        )

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OdcsSchemaError(
                [
                    OdcsDiagnostic(
                        code=CODE_SCHEMA,
                        path="",
                        message="document is not a finite JSON tree",
                    )
                ]
            )
        return value

    if isinstance(value, Mapping):
        obj_id = id(value)
        if obj_id in _stack:
            raise OdcsSchemaError(
                [
                    OdcsDiagnostic(
                        code=CODE_SCHEMA,
                        path="",
                        message="document is not a finite JSON tree",
                    )
                ]
            )
        nested_stack = _stack | {obj_id}
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise OdcsSchemaError(
                    [
                        OdcsDiagnostic(
                            code=CODE_SCHEMA,
                            path="",
                            message="document is not a finite JSON tree",
                        )
                    ]
                )
            result[key] = copy_json_tree(item, _depth=_depth + 1, _stack=nested_stack)
        return result

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        arr_id = id(value)
        if arr_id in _stack:
            raise OdcsSchemaError(
                [
                    OdcsDiagnostic(
                        code=CODE_SCHEMA,
                        path="",
                        message="document is not a finite JSON tree",
                    )
                ]
            )
        nested_stack = _stack | {arr_id}
        return [copy_json_tree(item, _depth=_depth + 1, _stack=nested_stack) for item in value]

    raise OdcsSchemaError(
        [
            OdcsDiagnostic(
                code=CODE_SCHEMA,
                path="",
                message="document is not a finite JSON tree",
            )
        ]
    )


def validate_odcs_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an ODCS document for this integration (v3.1.0 only).

    Returns a deep-copied validated mapping. Does not mutate the caller input.
    """
    copied = copy_json_tree(document)
    if not isinstance(copied, dict):
        raise OdcsSchemaError(
            [
                OdcsDiagnostic(
                    code=CODE_SCHEMA,
                    path="",
                    message="ODCS document root must be a mapping",
                )
            ]
        )

    api_version = copied.get("apiVersion")
    if "apiVersion" not in copied:
        raise OdcsSchemaError(
            [
                OdcsDiagnostic(
                    code=CODE_SCHEMA,
                    path="/apiVersion",
                    message="missing required property",
                )
            ]
        )
    if api_version != SUPPORTED_API_VERSION:
        raise OdcsUnsupportedVersionError(
            [
                OdcsDiagnostic(
                    code=CODE_UNSUPPORTED_VERSION,
                    path="/apiVersion",
                    message="unsupported ODCS apiVersion",
                )
            ]
        )

    diagnostics: list[OdcsDiagnostic] = []
    seen: set[tuple[str, str, str]] = set()
    for error in sorted(
        _get_validator().iter_errors(copied),
        key=lambda err: (
            list(err.absolute_path),
            err.validator or "",
            err.message,
        ),
    ):
        for diagnostic in _diagnostics_from_error(error):
            key = (diagnostic.path, diagnostic.code, diagnostic.message)
            if key in seen:
                continue
            seen.add(key)
            diagnostics.append(diagnostic)

    if diagnostics:
        raise OdcsSchemaError(diagnostics)
    return copied
