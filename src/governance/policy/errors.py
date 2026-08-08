"""Policy load/validation errors and machine-readable diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DIAGNOSTIC_SCHEMA = "governance-policy-diagnostics"
DIAGNOSTIC_VERSION = "1"

CODE_PARSE = "parse_error"
CODE_MISSING = "missing_policy_file"
CODE_UNSUPPORTED = "unsupported_policy_version"
CODE_SCHEMA = "schema_validation_failed"
CODE_SEMANTIC = "semantic_validation_failed"
CODE_DUPLICATE = "duplicate_policy_id"


@dataclass(frozen=True, slots=True)
class PolicyDiagnosticError:
    code: str
    path: str
    message: str
    source: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "source": self.source,
        }


class PolicyError(Exception):
    """Base class for policy file validation failures."""

    def __init__(
        self, errors: list[PolicyDiagnosticError] | tuple[PolicyDiagnosticError, ...]
    ) -> None:
        self.errors = tuple(sorted(errors, key=lambda e: (e.source, e.path, e.code, e.message)))
        super().__init__(self.errors[0].message if self.errors else "invalid policy")


class PolicyParseError(PolicyError):
    """YAML parse or file read failure."""


class PolicySchemaError(PolicyError):
    """JSON Schema structural failure."""


class PolicySemanticError(PolicyError):
    """Semantic validation failure."""


class UnsupportedPolicyVersionError(PolicyError):
    """Unsupported policy_version."""


def policy_diagnostics_failure(
    errors: list[PolicyDiagnosticError] | tuple[PolicyDiagnosticError, ...],
) -> dict[str, Any]:
    ordered = sorted(errors, key=lambda e: (e.source, e.path, e.code, e.message))
    return {
        "diagnostic_schema": DIAGNOSTIC_SCHEMA,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "errors": [error.to_dict() for error in ordered],
        "ok": False,
    }
