"""Drift diagnostics and errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governance.comparison.load import ComparisonArtifactError

DIAGNOSTIC_SCHEMA = "governance-drift-diagnostics"
DIAGNOSTIC_VERSION = "1"

CODE_COMPARISON_READ_ERROR = "comparison_read_error"
CODE_COMPARISON_PARSE_ERROR = "comparison_parse_error"
CODE_INVALID_COMPARISON_ARTIFACT = "invalid_comparison_artifact"
CODE_UNSUPPORTED_COMPARISON_SCHEMA = "unsupported_comparison_schema"
CODE_UNSUPPORTED_COMPARISON_VERSION = "unsupported_comparison_version"
CODE_COMPARISON_INTEGRITY_MISMATCH = "comparison_integrity_mismatch"
CODE_POLICY_READ_ERROR = "policy_read_error"
CODE_POLICY_PARSE_ERROR = "policy_parse_error"
CODE_INVALID_POLICY = "invalid_policy"
CODE_AMBIGUOUS_DRIFT_POLICY = "ambiguous_drift_policy"
CODE_MISSING_DRIFT_POLICY = "missing_drift_policy"
CODE_WRITE_ERROR = "write_error"

ALL_DIAGNOSTIC_CODES = frozenset(
    {
        CODE_COMPARISON_READ_ERROR,
        CODE_COMPARISON_PARSE_ERROR,
        CODE_INVALID_COMPARISON_ARTIFACT,
        CODE_UNSUPPORTED_COMPARISON_SCHEMA,
        CODE_UNSUPPORTED_COMPARISON_VERSION,
        CODE_COMPARISON_INTEGRITY_MISMATCH,
        CODE_POLICY_READ_ERROR,
        CODE_POLICY_PARSE_ERROR,
        CODE_INVALID_POLICY,
        CODE_AMBIGUOUS_DRIFT_POLICY,
        CODE_MISSING_DRIFT_POLICY,
        CODE_WRITE_ERROR,
    }
)

_ARTIFACT_CODE_MAP = {
    "read_error": CODE_COMPARISON_READ_ERROR,
    "parse_error": CODE_COMPARISON_PARSE_ERROR,
    "invalid_artifact": CODE_INVALID_COMPARISON_ARTIFACT,
    "unsupported_schema": CODE_UNSUPPORTED_COMPARISON_SCHEMA,
    "unsupported_version": CODE_UNSUPPORTED_COMPARISON_VERSION,
    "integrity_mismatch": CODE_COMPARISON_INTEGRITY_MISMATCH,
}


@dataclass(frozen=True, slots=True)
class DiagnosticError:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


class DriftError(RuntimeError):
    """Drift analysis failure with structured diagnostics."""

    def __init__(self, errors: list[DiagnosticError]) -> None:
        if not errors:
            raise ValueError("DriftError requires at least one diagnostic")
        self.errors = list(errors)
        super().__init__(errors[0].message)


def drift_diagnostics_failure(errors: list[DiagnosticError]) -> dict[str, Any]:
    sorted_errors = sorted(errors, key=lambda item: (item.path, item.code, item.message))
    return {
        "diagnostic_schema": DIAGNOSTIC_SCHEMA,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "errors": [item.to_dict() for item in sorted_errors],
        "ok": False,
    }


def map_comparison_artifact_error(exc: ComparisonArtifactError) -> list[DiagnosticError]:
    mapped: list[DiagnosticError] = []
    for item in exc.errors:
        code = _ARTIFACT_CODE_MAP.get(item.code, CODE_INVALID_COMPARISON_ARTIFACT)
        mapped.append(DiagnosticError(code=code, path=item.path, message=item.message))
    return sorted(mapped, key=lambda entry: (entry.path, entry.code, entry.message))
