"""JSON Schema validation for saved .gplan artifacts."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from governance.plans.errors import (
    CODE_SCHEMA,
    CODE_UNSUPPORTED,
    PlanDiagnosticError,
    PlanSchemaError,
    UnsupportedPlanVersionError,
)

try:
    from importlib.resources import files
except ImportError:  # pragma: no cover
    from importlib_resources import files  # type: ignore[no-redef]

_SCHEMA_BY_VERSION = {
    "1": "governance-plan.v1.schema.json",
    "2": "governance-plan.v2.schema.json",
}
_validators: dict[str, Draft202012Validator] = {}


def load_schema(plan_version: str = "1") -> dict[str, Any]:
    import json

    resource = _SCHEMA_BY_VERSION.get(plan_version)
    if resource is None:
        raise UnsupportedPlanVersionError(
            [
                PlanDiagnosticError(
                    code=CODE_UNSUPPORTED,
                    path="/plan_version",
                    message="unsupported plan_version",
                )
            ]
        )
    text = files("governance.plans.schemas").joinpath(resource).read_text(encoding="utf-8")
    return json.loads(text)


def _get_validator(plan_version: str) -> Draft202012Validator:
    cached = _validators.get(plan_version)
    if cached is not None:
        return cached
    validator = Draft202012Validator(load_schema(plan_version))
    _validators[plan_version] = validator
    return validator


def _pointer_from_path(path: list[Any]) -> str:
    if not path:
        return ""
    parts = [str(item).replace("~", "~0").replace("/", "~1") for item in path]
    return "/" + "/".join(parts)


def validate_plan_structure(document: Any) -> None:
    if not isinstance(document, dict):
        raise PlanSchemaError(
            [
                PlanDiagnosticError(
                    code=CODE_SCHEMA,
                    path="",
                    message="plan root must be a mapping",
                )
            ]
        )
    version = document.get("plan_version")
    if version not in _SCHEMA_BY_VERSION:
        raise UnsupportedPlanVersionError(
            [
                PlanDiagnosticError(
                    code=CODE_UNSUPPORTED,
                    path="/plan_version",
                    message="unsupported plan_version",
                )
            ]
        )

    errors: list[PlanDiagnosticError] = []
    for error in sorted(
        _get_validator(str(version)).iter_errors(document),
        key=lambda err: list(err.absolute_path),
    ):
        path = _pointer_from_path(list(error.absolute_path))
        if error.validator == "const" and list(error.absolute_path) == ["plan_version"]:
            raise UnsupportedPlanVersionError(
                [
                    PlanDiagnosticError(
                        code=CODE_UNSUPPORTED,
                        path="/plan_version",
                        message="unsupported plan_version",
                    )
                ]
            )
        message = error.message
        errors.append(PlanDiagnosticError(code=CODE_SCHEMA, path=path, message=message))
    if errors:
        raise PlanSchemaError(errors)
