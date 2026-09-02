"""History diagnostics and errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governance.authority.errors import (
    CODE_PARSE as AUTH_CODE_PARSE,
)
from governance.authority.errors import (
    AuthorityError,
)
from governance.comparison.errors import ComparisonError
from governance.comparison.errors import DiagnosticError as ComparisonDiagnostic
from governance.observations.artifact import ObservationsArtifactError
from governance.snapshots.errors import SnapshotError

DIAGNOSTIC_SCHEMA = "governance-history-diagnostics"
DIAGNOSTIC_VERSION = "1"

CODE_HISTORY_READ_ERROR = "history_read_error"
CODE_HISTORY_PARSE_ERROR = "history_parse_error"
CODE_INVALID_HISTORY_ARTIFACT = "invalid_history_artifact"
CODE_UNSUPPORTED_HISTORY_SCHEMA = "unsupported_history_schema"
CODE_UNSUPPORTED_HISTORY_VERSION = "unsupported_history_version"
CODE_HISTORY_INTEGRITY_MISMATCH = "history_integrity_mismatch"
CODE_DUPLICATE_HISTORY_STATE = "duplicate_history_state"
CODE_SNAPSHOT_READ_ERROR = "snapshot_read_error"
CODE_SNAPSHOT_PARSE_ERROR = "snapshot_parse_error"
CODE_INVALID_SNAPSHOT_ARTIFACT = "invalid_snapshot_artifact"
CODE_UNSUPPORTED_SNAPSHOT_SCHEMA = "unsupported_snapshot_schema"
CODE_UNSUPPORTED_SNAPSHOT_VERSION = "unsupported_snapshot_version"
CODE_SNAPSHOT_INTEGRITY_MISMATCH = "snapshot_integrity_mismatch"
CODE_CONTEXT_READ_ERROR = "context_read_error"
CODE_CONTEXT_PARSE_ERROR = "context_parse_error"
CODE_INVALID_CONTEXT_ARTIFACT = "invalid_context_artifact"
CODE_CONTEXT_INTEGRITY_MISMATCH = "context_integrity_mismatch"
CODE_OBJECT_NOT_FOUND = "object_not_found"
CODE_PROPERTY_NOT_FOUND = "property_not_found"
CODE_WRITE_ERROR = "write_error"

ALL_DIAGNOSTIC_CODES = frozenset(
    {
        CODE_HISTORY_READ_ERROR,
        CODE_HISTORY_PARSE_ERROR,
        CODE_INVALID_HISTORY_ARTIFACT,
        CODE_UNSUPPORTED_HISTORY_SCHEMA,
        CODE_UNSUPPORTED_HISTORY_VERSION,
        CODE_HISTORY_INTEGRITY_MISMATCH,
        CODE_DUPLICATE_HISTORY_STATE,
        CODE_SNAPSHOT_READ_ERROR,
        CODE_SNAPSHOT_PARSE_ERROR,
        CODE_INVALID_SNAPSHOT_ARTIFACT,
        CODE_UNSUPPORTED_SNAPSHOT_SCHEMA,
        CODE_UNSUPPORTED_SNAPSHOT_VERSION,
        CODE_SNAPSHOT_INTEGRITY_MISMATCH,
        CODE_CONTEXT_READ_ERROR,
        CODE_CONTEXT_PARSE_ERROR,
        CODE_INVALID_CONTEXT_ARTIFACT,
        CODE_CONTEXT_INTEGRITY_MISMATCH,
        CODE_OBJECT_NOT_FOUND,
        CODE_PROPERTY_NOT_FOUND,
        CODE_WRITE_ERROR,
    }
)

_SNAPSHOT_CODE_MAP: dict[str, str] = {
    "read_error": CODE_SNAPSHOT_READ_ERROR,
    "parse_error": CODE_SNAPSHOT_PARSE_ERROR,
    "invalid_snapshot_root": CODE_INVALID_SNAPSHOT_ARTIFACT,
    "invalid_snapshot_payload": CODE_INVALID_SNAPSHOT_ARTIFACT,
    "unsupported_snapshot_schema": CODE_UNSUPPORTED_SNAPSHOT_SCHEMA,
    "unsupported_snapshot_version": CODE_UNSUPPORTED_SNAPSHOT_VERSION,
    "missing_content_identity": CODE_SNAPSHOT_INTEGRITY_MISMATCH,
    "integrity_mismatch": CODE_SNAPSHOT_INTEGRITY_MISMATCH,
    "write_error": CODE_WRITE_ERROR,
}

_SNAPSHOT_MESSAGES: dict[str, str] = {
    CODE_SNAPSHOT_READ_ERROR: "unable to read snapshot",
    CODE_SNAPSHOT_PARSE_ERROR: "invalid snapshot JSON",
    CODE_INVALID_SNAPSHOT_ARTIFACT: "invalid snapshot artifact",
    CODE_UNSUPPORTED_SNAPSHOT_SCHEMA: "unsupported snapshot schema",
    CODE_UNSUPPORTED_SNAPSHOT_VERSION: "unsupported snapshot version",
    CODE_SNAPSHOT_INTEGRITY_MISMATCH: "snapshot content_identity mismatch",
}


@dataclass(frozen=True, slots=True)
class DiagnosticError:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


class HistoryError(RuntimeError):
    """History analysis or artifact failure with structured diagnostics."""

    def __init__(self, errors: list[DiagnosticError]) -> None:
        if not errors:
            raise ValueError("HistoryError requires at least one diagnostic")
        self.errors = list(errors)
        super().__init__(errors[0].message)


def history_diagnostics_failure(errors: list[DiagnosticError]) -> dict[str, Any]:
    sorted_errors = sorted(errors, key=lambda item: (item.path, item.code, item.message))
    return {
        "diagnostic_schema": DIAGNOSTIC_SCHEMA,
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "errors": [item.to_dict() for item in sorted_errors],
        "ok": False,
    }


def map_snapshot_error(exc: SnapshotError, *, entry_path: str = "/") -> DiagnosticError:
    """Map snapshot load errors to bounded history diagnostics without host paths."""
    raw_code = getattr(exc, "code", "invalid_snapshot_payload")
    mapped = _SNAPSHOT_CODE_MAP.get(raw_code, CODE_INVALID_SNAPSHOT_ARTIFACT)
    raw_path = getattr(exc, "path", "/")
    if entry_path in {"", "/"}:
        logical = raw_path if raw_path else "/"
    elif raw_path in {"", "/"}:
        logical = entry_path
    else:
        logical = f"{entry_path}{raw_path}"
    message = _SNAPSHOT_MESSAGES.get(mapped, _SNAPSHOT_MESSAGES[CODE_INVALID_SNAPSHOT_ARTIFACT])
    return DiagnosticError(code=mapped, path=logical, message=message)


def map_observations_error(
    exc: ObservationsArtifactError,
    *,
    entry_path: str,
) -> list[DiagnosticError]:
    """Map observations artifact errors into context diagnostics."""
    mapped: list[DiagnosticError] = []
    for item in exc.errors:
        if item.code == "read_error":
            code = CODE_CONTEXT_READ_ERROR
            message = "unable to read observations artifact"
        elif item.code == "parse_error":
            code = CODE_CONTEXT_PARSE_ERROR
            message = "invalid observations JSON"
        elif item.code == "unsupported_schema":
            code = CODE_INVALID_CONTEXT_ARTIFACT
            message = "unsupported observations schema"
        elif item.code == "unsupported_version":
            code = CODE_INVALID_CONTEXT_ARTIFACT
            message = "unsupported observations version"
        elif item.code == "integrity_mismatch":
            code = CODE_CONTEXT_INTEGRITY_MISMATCH
            message = "observations content_identity mismatch"
        else:
            code = CODE_INVALID_CONTEXT_ARTIFACT
            message = "invalid observations artifact"
        raw_path = item.path if item.path else "/"
        if raw_path in {"", "/"}:
            logical = f"{entry_path}/context/observations"
        else:
            logical = f"{entry_path}/context/observations{raw_path}"
        mapped.append(DiagnosticError(code=code, path=logical, message=message))
    return mapped


def map_authority_error(exc: AuthorityError, *, entry_path: str) -> list[DiagnosticError]:
    """Map authority errors into bounded context diagnostics without host paths."""
    mapped: list[DiagnosticError] = []
    for item in sorted(exc.errors, key=lambda e: (e.path, e.code, e.message)):
        if item.code == AUTH_CODE_PARSE:
            code = CODE_CONTEXT_PARSE_ERROR
            message = "invalid authority YAML"
        elif item.code == "missing_authority_file":
            code = CODE_CONTEXT_READ_ERROR
            message = "unable to read authority file"
        else:
            code = CODE_INVALID_CONTEXT_ARTIFACT
            message = "invalid authority artifact"
        index = item.file_index
        if index is None:
            logical = f"{entry_path}/context/authority"
        else:
            logical = f"{entry_path}/context/authority/{index}"
        if item.path:
            logical = f"{logical}{item.path if item.path.startswith('/') else '/' + item.path}"
        mapped.append(DiagnosticError(code=code, path=logical, message=message))
    return mapped


def map_comparison_error(
    exc: ComparisonError,
    *,
    candidate_entry_index: int,
) -> list[DiagnosticError]:
    """Map pairwise comparison failures onto the candidate entry path."""
    base = f"/entries/{candidate_entry_index}"
    mapped: list[DiagnosticError] = []
    for item in exc.errors:
        if isinstance(item, ComparisonDiagnostic):
            raw_path = item.path if item.path else "/"
            logical = base if raw_path in {"", "/"} else f"{base}{raw_path}"
            mapped.append(
                DiagnosticError(
                    code=item.code,
                    path=logical,
                    message=item.message,
                )
            )
        else:
            mapped.append(
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=base,
                    message="incompatible history timeline adjacency",
                )
            )
    return mapped


__all__ = [
    "ALL_DIAGNOSTIC_CODES",
    "CODE_CONTEXT_INTEGRITY_MISMATCH",
    "CODE_CONTEXT_PARSE_ERROR",
    "CODE_CONTEXT_READ_ERROR",
    "CODE_DUPLICATE_HISTORY_STATE",
    "CODE_HISTORY_INTEGRITY_MISMATCH",
    "CODE_HISTORY_PARSE_ERROR",
    "CODE_HISTORY_READ_ERROR",
    "CODE_INVALID_CONTEXT_ARTIFACT",
    "CODE_INVALID_HISTORY_ARTIFACT",
    "CODE_INVALID_SNAPSHOT_ARTIFACT",
    "CODE_OBJECT_NOT_FOUND",
    "CODE_PROPERTY_NOT_FOUND",
    "CODE_SNAPSHOT_INTEGRITY_MISMATCH",
    "CODE_SNAPSHOT_PARSE_ERROR",
    "CODE_SNAPSHOT_READ_ERROR",
    "CODE_UNSUPPORTED_HISTORY_SCHEMA",
    "CODE_UNSUPPORTED_HISTORY_VERSION",
    "CODE_UNSUPPORTED_SNAPSHOT_SCHEMA",
    "CODE_UNSUPPORTED_SNAPSHOT_VERSION",
    "CODE_WRITE_ERROR",
    "DIAGNOSTIC_SCHEMA",
    "DIAGNOSTIC_VERSION",
    "DiagnosticError",
    "HistoryError",
    "history_diagnostics_failure",
    "map_authority_error",
    "map_comparison_error",
    "map_observations_error",
    "map_snapshot_error",
]
