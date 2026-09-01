"""Reconciliation diagnostics contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RECONCILIATION_DIAGNOSTIC_SCHEMA = "governance-reconciliation-diagnostics"
RECONCILIATION_DIAGNOSTIC_VERSION = "1"

CODE_SOURCE_ERROR = "source_error"
CODE_OBJECT_IDENTITY_CONFLICT = "reconciliation_object_identity_conflict"
CODE_UNRESOLVED_PROPERTY_CONFLICT = "unresolved_property_conflict"
CODE_INVALID_OR_AMBIGUOUS_AUTHORITY = "invalid_or_ambiguous_authority"
CODE_UNSUPPORTED_EFFECTIVE_VALUE = "unsupported_effective_value"

EXPLAIN_DIAGNOSTIC_SCHEMA = "governance-explain-diagnostics"
EXPLAIN_DIAGNOSTIC_VERSION = "1"

CODE_READ_ERROR = "read_error"
CODE_PARSE_ERROR = "parse_error"
CODE_INVALID_OBJECT_IDENTITY = "invalid_object_identity"
CODE_NAMESPACE_MISMATCH = "namespace_mismatch"
CODE_UNKNOWN_OBJECT = "unknown_object"
CODE_UNKNOWN_PROPERTY = "unknown_property"
CODE_WRITE_ERROR = "write_error"


@dataclass(frozen=True, slots=True)
class DiagnosticError:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


def _sorted_errors(
    errors: list[DiagnosticError] | tuple[DiagnosticError, ...],
) -> list[dict[str, str]]:
    ordered = sorted(errors, key=lambda item: (item.path, item.code, item.message))
    return [item.to_dict() for item in ordered]


def reconciliation_diagnostics_failure(
    errors: list[DiagnosticError] | tuple[DiagnosticError, ...],
) -> dict[str, Any]:
    return {
        "diagnostic_schema": RECONCILIATION_DIAGNOSTIC_SCHEMA,
        "diagnostic_version": RECONCILIATION_DIAGNOSTIC_VERSION,
        "errors": _sorted_errors(errors),
        "ok": False,
    }


def explain_diagnostics_failure(
    errors: list[DiagnosticError] | tuple[DiagnosticError, ...],
) -> dict[str, Any]:
    return {
        "diagnostic_schema": EXPLAIN_DIAGNOSTIC_SCHEMA,
        "diagnostic_version": EXPLAIN_DIAGNOSTIC_VERSION,
        "errors": _sorted_errors(errors),
        "ok": False,
    }


class ReconciliationError(Exception):
    """Plan/apply reconciliation validation failure (exit 4)."""

    def __init__(self, errors: list[DiagnosticError] | tuple[DiagnosticError, ...]) -> None:
        self.errors = tuple(errors)
        super().__init__(self.errors[0].message if self.errors else "reconciliation error")

    def to_diagnostics(self) -> dict[str, Any]:
        return reconciliation_diagnostics_failure(self.errors)


class ExplainError(Exception):
    """Explain validation failure (exit 4)."""

    def __init__(self, errors: list[DiagnosticError] | tuple[DiagnosticError, ...]) -> None:
        self.errors = tuple(errors)
        super().__init__(self.errors[0].message if self.errors else "explain error")

    def to_diagnostics(self) -> dict[str, Any]:
        return explain_diagnostics_failure(self.errors)


def rfc6901_escape_segment(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def objects_diagnostic_path(local_id: str) -> str:
    return f"/objects/{rfc6901_escape_segment(local_id)}"
