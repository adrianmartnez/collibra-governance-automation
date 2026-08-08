"""Machine-readable diagnostics for runtime config resolution failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RESOLUTION_DIAGNOSTIC_SCHEMA = "governance-config-resolution-diagnostics"
RESOLUTION_DIAGNOSTIC_VERSION = "1"

CODE_ENV_UNRESOLVED = "environment_reference_unresolved"
CODE_RUNTIME_INVALID = "runtime_configuration_invalid"


@dataclass(frozen=True, slots=True)
class ResolutionDiagnosticError:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


def resolution_diagnostics_failure(
    errors: list[ResolutionDiagnosticError] | tuple[ResolutionDiagnosticError, ...],
) -> dict[str, Any]:
    ordered = sorted(errors, key=lambda e: (e.path, e.code, e.message))
    return {
        "diagnostic_schema": RESOLUTION_DIAGNOSTIC_SCHEMA,
        "diagnostic_version": RESOLUTION_DIAGNOSTIC_VERSION,
        "errors": [error.to_dict() for error in ordered],
        "ok": False,
    }


def unresolved_env_diagnostic(
    *,
    path: str,
    message: str = "required environment reference could not be resolved",
) -> dict[str, Any]:
    return resolution_diagnostics_failure(
        [
            ResolutionDiagnosticError(
                code=CODE_ENV_UNRESOLVED,
                path=path,
                message=message,
            )
        ]
    )


def runtime_invalid_diagnostic(
    *,
    path: str,
    message: str,
) -> dict[str, Any]:
    return resolution_diagnostics_failure(
        [
            ResolutionDiagnosticError(
                code=CODE_RUNTIME_INVALID,
                path=path,
                message=message,
            )
        ]
    )
