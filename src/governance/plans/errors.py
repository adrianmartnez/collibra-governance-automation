"""Saved-plan load/validation errors and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PLAN_DIAGNOSTIC_SCHEMA = "governance-plan-diagnostics"
PLAN_DIAGNOSTIC_VERSION = "1"

CODE_PARSE = "parse_error"
CODE_UNSUPPORTED = "unsupported_plan_version"
CODE_SCHEMA = "schema_validation_failed"
CODE_IDENTITY = "content_identity_mismatch"
CODE_MALFORMED_ACTION = "malformed_action"
CODE_UNSUPPORTED_ACTION = "unsupported_action_type"

STALE_RESULT_SCHEMA = "governance-stale-plan-result"
STALE_RESULT_VERSION = "1"

APPLY_RESULT_SCHEMA = "governance-apply-result"
APPLY_RESULT_VERSION = "1"


@dataclass(frozen=True, slots=True)
class PlanDiagnosticError:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


class PlanError(Exception):
    def __init__(self, errors: list[PlanDiagnosticError] | tuple[PlanDiagnosticError, ...]) -> None:
        self.errors = tuple(sorted(errors, key=lambda e: (e.path, e.code, e.message)))
        super().__init__(self.errors[0].message if self.errors else "invalid plan")


class PlanParseError(PlanError):
    pass


class PlanSchemaError(PlanError):
    pass


class UnsupportedPlanVersionError(PlanError):
    pass


class PlanIntegrityError(PlanError):
    pass


def plan_diagnostics_failure(
    errors: list[PlanDiagnosticError] | tuple[PlanDiagnosticError, ...],
) -> dict[str, Any]:
    ordered = sorted(errors, key=lambda e: (e.path, e.code, e.message))
    return {
        "diagnostic_schema": PLAN_DIAGNOSTIC_SCHEMA,
        "diagnostic_version": PLAN_DIAGNOSTIC_VERSION,
        "errors": [error.to_dict() for error in ordered],
        "ok": False,
    }
