"""Comparison diagnostics and errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governance.snapshots.errors import SnapshotError

DIAGNOSTIC_SCHEMA = "governance-comparison-diagnostics"
DIAGNOSTIC_VERSION = "1"

CODE_READ_ERROR = "read_error"
CODE_PARSE_ERROR = "parse_error"
CODE_INVALID_SNAPSHOT_ROOT = "invalid_snapshot_root"
CODE_UNSUPPORTED_SNAPSHOT_SCHEMA = "unsupported_snapshot_schema"
CODE_UNSUPPORTED_SNAPSHOT_VERSION = "unsupported_snapshot_version"
CODE_MISSING_CONTENT_IDENTITY = "missing_content_identity"
CODE_INTEGRITY_MISMATCH = "integrity_mismatch"
CODE_INVALID_SNAPSHOT_PAYLOAD = "invalid_snapshot_payload"
CODE_SNAPSHOT_ENVELOPE_MISMATCH = "snapshot_envelope_mismatch"
CODE_SCANNER_CONTRACT_MISMATCH = "scanner_contract_mismatch"
CODE_SYSTEM_TYPE_MISMATCH = "system_type_mismatch"
CODE_SCANNER_MISMATCH = "scanner_mismatch"
CODE_ROOT_ALIGNMENT_REQUIRED = "root_alignment_required"
CODE_DUPLICATE_SNAPSHOT_OBJECT_ID = "duplicate_snapshot_object_id"
CODE_DUPLICATE_COMPARISON_IDENTITY = "duplicate_comparison_identity"
CODE_INVALID_SNAPSHOT_REFERENCES = "invalid_snapshot_references"
CODE_WRITE_ERROR = "write_error"

ALL_DIAGNOSTIC_CODES = frozenset(
    {
        CODE_READ_ERROR,
        CODE_PARSE_ERROR,
        CODE_INVALID_SNAPSHOT_ROOT,
        CODE_UNSUPPORTED_SNAPSHOT_SCHEMA,
        CODE_UNSUPPORTED_SNAPSHOT_VERSION,
        CODE_MISSING_CONTENT_IDENTITY,
        CODE_INTEGRITY_MISMATCH,
        CODE_INVALID_SNAPSHOT_PAYLOAD,
        CODE_SNAPSHOT_ENVELOPE_MISMATCH,
        CODE_SCANNER_CONTRACT_MISMATCH,
        CODE_SYSTEM_TYPE_MISMATCH,
        CODE_SCANNER_MISMATCH,
        CODE_ROOT_ALIGNMENT_REQUIRED,
        CODE_DUPLICATE_SNAPSHOT_OBJECT_ID,
        CODE_DUPLICATE_COMPARISON_IDENTITY,
        CODE_INVALID_SNAPSHOT_REFERENCES,
        CODE_WRITE_ERROR,
    }
)


_SNAPSHOT_COMPARE_MESSAGES: dict[str, str] = {
    CODE_READ_ERROR: "unable to read snapshot",
    CODE_PARSE_ERROR: "invalid snapshot JSON",
    CODE_INVALID_SNAPSHOT_ROOT: "snapshot root must be a mapping",
    CODE_UNSUPPORTED_SNAPSHOT_SCHEMA: "unsupported snapshot schema",
    CODE_UNSUPPORTED_SNAPSHOT_VERSION: "unsupported snapshot version",
    CODE_MISSING_CONTENT_IDENTITY: "snapshot content_identity is required",
    CODE_INTEGRITY_MISMATCH: "snapshot content_identity mismatch",
    CODE_INVALID_SNAPSHOT_PAYLOAD: "invalid snapshot payload",
}


def snapshot_compare_diagnostic(exc: SnapshotError, *, side: str) -> DiagnosticError:
    """Map snapshot load errors to bounded comparison diagnostics without host paths."""
    code = getattr(exc, "code", CODE_INVALID_SNAPSHOT_PAYLOAD)
    raw_path = getattr(exc, "path", "/")
    mapped_path = f"/{side}" if raw_path in {"", "/"} else f"/{side}{raw_path}"
    message = _SNAPSHOT_COMPARE_MESSAGES.get(
        code,
        _SNAPSHOT_COMPARE_MESSAGES[CODE_INVALID_SNAPSHOT_PAYLOAD],
    )
    return DiagnosticError(code=code, path=mapped_path, message=message)


@dataclass(frozen=True, slots=True)
class DiagnosticError:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


class ComparisonError(RuntimeError):
    """Comparison analysis failure with structured diagnostics."""

    def __init__(self, errors: list[DiagnosticError]) -> None:
        if not errors:
            raise ValueError("ComparisonError requires at least one diagnostic")
        self.errors = list(errors)
        super().__init__(errors[0].message)


def comparison_diagnostics_failure(errors: list[DiagnosticError]) -> dict[str, Any]:
    sorted_errors = sorted(errors, key=lambda item: (item.path, item.code, item.message))
    return {
        "diagnostic_schema": DIAGNOSTIC_SCHEMA,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "errors": [item.to_dict() for item in sorted_errors],
        "ok": False,
    }
