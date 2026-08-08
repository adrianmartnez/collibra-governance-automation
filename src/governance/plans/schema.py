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

_SCHEMA_RESOURCE = "governance-plan.v1.schema.json"
_validator: Draft202012Validator | None = None


def load_schema() -> dict[str, Any]:
    import json

    text = files("governance.plans.schemas").joinpath(_SCHEMA_RESOURCE).read_text(encoding="utf-8")
    return json.loads(text)


def _get_validator() -> Draft202012Validator:
    global _validator
    if _validator is None:
        _validator = Draft202012Validator(load_schema())
    return _validator


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
    if version is not None and version != "1":
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
        _get_validator().iter_errors(document),
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
        message = "plan failed structural validation"
        if error.validator == "additionalProperties":
            message = "unknown property is not allowed"
        elif error.validator == "required":
            message = "missing required property"
        errors.append(PlanDiagnosticError(code=CODE_SCHEMA, path=path, message=message))
    if errors:
        raise PlanSchemaError(errors)
