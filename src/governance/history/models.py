"""Governance-history v1 models and normalization helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from governance.history.errors import (
    CODE_INVALID_HISTORY_ARTIFACT,
    DiagnosticError,
    HistoryError,
)
from governance.identity.hashing import (
    ContentIdentity,
    history_entry_state_identity,
    history_identity,
)

HISTORY_SCHEMA = "governance-history"
HISTORY_VERSION = "1"
EVOLUTION_SCHEMA = "governance-history-evolution"
EVOLUTION_VERSION = "1"
DIAGNOSTIC_SCHEMA = "governance-history-diagnostics"

_MAX_LABELS = 32
_MAX_LABEL_KEY_LEN = 64
_MAX_LABEL_VALUE_LEN = 256

_RESERVED_LABEL_KEYS = frozenset(
    {
        "password",
        "token",
        "client_secret",
        "authorization",
        "credentials",
        "api_key",
        "secret",
    }
)

_CAPTURED_AT_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})(?P<frac>\.\d+)?Z$"
)

_CONTENT_IDENTITY_KEYS = frozenset({"algorithm", "digest", "hashing_contract_version"})


def normalize_history_relative_path(raw: str, *, path: str = "/") -> str:
    """Normalize a relative POSIX path from the history parent; allow ``..``."""
    if not isinstance(raw, str) or not raw.strip():
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="path must be a non-empty relative string",
                )
            ]
        )
    if "\x00" in raw:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="path must not contain NUL",
                )
            ]
        )
    text = raw.strip().replace("\\", "/")
    if text.startswith("//"):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="UNC paths are not allowed",
                )
            ]
        )
    if text.startswith("/"):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="absolute paths are not allowed",
                )
            ]
        )
    if len(text) >= 2 and text[1] == ":":
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="drive-qualified paths are not allowed",
                )
            ]
        )

    parts: list[str] = []
    for part in PurePosixPath(text).parts:
        if part in ("", "."):
            continue
        if part.upper() == "NUL":
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=path,
                        message="NUL device paths are not allowed",
                    )
                ]
            )
        parts.append(part)
    if not parts:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="path must be a non-empty relative string",
                )
            ]
        )
    return "/".join(parts)


def normalize_labels(
    labels: Mapping[str, str] | None,
    *,
    path: str = "/labels",
) -> dict[str, str] | None:
    """Normalize optional labels; reject duplicates after trim and size limits."""
    if labels is None:
        return None
    if not isinstance(labels, Mapping):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="labels must be a mapping",
                )
            ]
        )
    if len(labels) > _MAX_LABELS:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="labels exceed maximum of 32 entries",
                )
            ]
        )

    normalized: dict[str, str] = {}
    for raw_key, raw_value in labels.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=path,
                        message="label keys and values must be strings",
                    )
                ]
            )
        key = raw_key.strip()
        value = raw_value.strip()
        if not key or not value:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=path,
                        message="label keys and values must be non-empty after trim",
                    )
                ]
            )
        if key.casefold() in _RESERVED_LABEL_KEYS:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=path,
                        message="label key is reserved",
                    )
                ]
            )
        if len(key) > _MAX_LABEL_KEY_LEN:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=path,
                        message="label key exceeds maximum length of 64",
                    )
                ]
            )
        if len(value) > _MAX_LABEL_VALUE_LEN:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=path,
                        message="label value exceeds maximum length of 256",
                    )
                ]
            )
        if key in normalized:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=path,
                        message="duplicate label key after trim",
                    )
                ]
            )
        normalized[key] = value
    return normalized


def validate_captured_at(value: str | None, *, path: str = "/captured_at") -> str | None:
    """Validate optional RFC3339 UTC timestamp (``Z`` / fractional ``Z`` only)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="captured_at must be an RFC3339 UTC timestamp",
                )
            ]
        )
    match = _CAPTURED_AT_RE.fullmatch(value)
    if match is None:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="captured_at must be an RFC3339 UTC timestamp",
                )
            ]
        )
    try:
        datetime.strptime(
            f"{match.group('date')}T{match.group('time')}",
            "%Y-%m-%dT%H:%M:%S",
        )
    except ValueError as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="captured_at must be an RFC3339 UTC timestamp",
                )
            ]
        ) from exc
    # fraction already validated by regex (one or more digits if present)
    return value


def parse_content_identity(raw: Any, *, path: str) -> ContentIdentity:
    if not isinstance(raw, dict) or set(raw) != _CONTENT_IDENTITY_KEYS:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="invalid content_identity",
                )
            ]
        )
    algorithm = raw["algorithm"]
    hashing_contract_version = raw["hashing_contract_version"]
    digest = raw["digest"]
    if (
        not isinstance(algorithm, str)
        or not isinstance(hashing_contract_version, str)
        or not isinstance(digest, str)
        or not algorithm
        or not hashing_contract_version
        or not digest
    ):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="invalid content_identity",
                )
            ]
        )
    return ContentIdentity(
        algorithm=algorithm,
        hashing_contract_version=hashing_contract_version,
        digest=digest,
    )


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    align_source_roots: bool
    align_database_roots: bool

    def __post_init__(self) -> None:
        if not isinstance(self.align_source_roots, bool):
            raise TypeError("align_source_roots must be bool")
        if not isinstance(self.align_database_roots, bool):
            raise TypeError("align_database_roots must be bool")

    def to_dict(self) -> dict[str, bool]:
        return {
            "align_database_roots": self.align_database_roots,
            "align_source_roots": self.align_source_roots,
        }


@dataclass(frozen=True, slots=True)
class HistoryEntryState:
    snapshot: ContentIdentity
    labels: dict[str, str] | None = None
    context: dict[str, Any] | None = None

    def to_identity_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"snapshot": self.snapshot.to_dict()}
        if self.labels is not None:
            payload["labels"] = dict(self.labels)
        if self.context is not None:
            payload["context"] = dict(self.context)
        return payload

    def entry_state_identity(self) -> ContentIdentity:
        return history_entry_state_identity(self.to_identity_dict())

    def context_form(self) -> str:
        if self.context is None:
            return "none"
        keys = set(self.context)
        if keys == {"observations"}:
            return "provenance"
        if keys == {"observations", "authority", "conflicts"}:
            return "full"
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/context",
                    message="invalid history context form",
                )
            ]
        )


@dataclass(frozen=True, slots=True)
class HistoryOperator:
    snapshot_path: str
    observations_path: str | None = None
    authority_paths: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"snapshot_path": self.snapshot_path}
        if self.observations_path is not None:
            payload["observations_path"] = self.observations_path
        if self.authority_paths is not None:
            payload["authority_paths"] = list(self.authority_paths)
        return payload


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    state: HistoryEntryState
    operator: HistoryOperator
    captured_at: str | None = None

    def to_persisted_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operator": self.operator.to_dict(),
            "state": self.state.to_identity_dict(),
        }
        if self.captured_at is not None:
            payload["captured_at"] = self.captured_at
        return payload


@dataclass(frozen=True, slots=True)
class GovernanceHistory:
    comparison_policy: ComparisonPolicy
    entries: tuple[HistoryEntry, ...]

    def canonical_dict_without_identity(self) -> dict[str, Any]:
        return {
            "comparison_policy": self.comparison_policy.to_dict(),
            "entries": [{"state": entry.state.to_identity_dict()} for entry in self.entries],
            "history_schema": HISTORY_SCHEMA,
            "history_version": HISTORY_VERSION,
        }

    def content_identity(self) -> ContentIdentity:
        return history_identity(self.canonical_dict_without_identity())

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict_without_identity()
        payload["entries"] = [entry.to_persisted_dict() for entry in self.entries]
        payload["content_identity"] = self.content_identity().to_dict()
        return payload


def validate_operator_context_coupling(
    state: HistoryEntryState,
    operator: HistoryOperator,
    *,
    entry_path: str,
) -> None:
    """Enforce required operator locators for the entry context form."""
    form = state.context_form()
    if form == "none":
        if operator.observations_path is not None or operator.authority_paths is not None:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=f"{entry_path}/operator",
                        message="no-context entry operator must contain only snapshot_path",
                    )
                ]
            )
        return
    if form == "provenance":
        if operator.observations_path is None or operator.authority_paths is not None:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=f"{entry_path}/operator",
                        message=(
                            "provenance entry operator requires snapshot_path and "
                            "observations_path without authority_paths"
                        ),
                    )
                ]
            )
        return
    if operator.observations_path is None or not operator.authority_paths:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=f"{entry_path}/operator",
                    message=(
                        "full-context entry operator requires snapshot_path, "
                        "observations_path, and non-empty authority_paths"
                    ),
                )
            ]
        )


__all__ = [
    "DIAGNOSTIC_SCHEMA",
    "EVOLUTION_SCHEMA",
    "EVOLUTION_VERSION",
    "HISTORY_SCHEMA",
    "HISTORY_VERSION",
    "ComparisonPolicy",
    "GovernanceHistory",
    "HistoryEntry",
    "HistoryEntryState",
    "HistoryOperator",
    "normalize_history_relative_path",
    "normalize_labels",
    "parse_content_identity",
    "validate_captured_at",
    "validate_operator_context_coupling",
]
