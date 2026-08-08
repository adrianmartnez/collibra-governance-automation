"""Config contract errors and machine-readable diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DIAGNOSTIC_SCHEMA = "governance-config-diagnostics"
DIAGNOSTIC_VERSION = "1"

CODE_PARSE = "parse_error"
CODE_SCHEMA = "schema_validation_failed"
CODE_SEMANTIC = "semantic_validation_failed"
CODE_UNSUPPORTED = "unsupported_config_version"


@dataclass(frozen=True, slots=True)
class DiagnosticError:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


class ConfigContractError(Exception):
    """Base class for governance.yaml validation failures."""

    def __init__(self, errors: list[DiagnosticError]) -> None:
        self.errors = tuple(sorted(errors, key=lambda e: (e.path, e.code, e.message)))
        super().__init__(self.errors[0].message if self.errors else "invalid configuration")


class ConfigParseError(ConfigContractError):
    """YAML parse failure."""


class ConfigSchemaError(ConfigContractError):
    """JSON Schema structural failure."""


class ConfigSemanticError(ConfigContractError):
    """Semantic validation failure."""


class UnsupportedConfigVersionError(ConfigContractError):
    """Unsupported schema_version."""


def diagnostics_failure(
    errors: list[DiagnosticError] | tuple[DiagnosticError, ...],
) -> dict[str, Any]:
    ordered = sorted(errors, key=lambda e: (e.path, e.code, e.message))
    return {
        "diagnostic_schema": DIAGNOSTIC_SCHEMA,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "errors": [error.to_dict() for error in ordered],
        "ok": False,
    }


def diagnostics_success(config_identity: dict[str, str]) -> dict[str, Any]:
    return {
        "config_identity": config_identity,
        "diagnostic_schema": DIAGNOSTIC_SCHEMA,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "errors": [],
        "ok": True,
    }
