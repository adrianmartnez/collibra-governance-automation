"""Strict load and full resolve for governance-history v1 artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from governance.comparison.align import RootAlignmentAck
from governance.comparison.errors import ComparisonError
from governance.comparison.projection import ProjectedSnapshot, project_snapshot
from governance.comparison.result import build_comparison_result
from governance.domain.authority import NormalizedAuthorityPolicySet
from governance.domain.conflicts import PropertyConflictReport
from governance.domain.observations import PropertyObservationSet
from governance.history.context import resolve_entry_context
from governance.history.errors import (
    CODE_HISTORY_INTEGRITY_MISMATCH,
    CODE_HISTORY_PARSE_ERROR,
    CODE_HISTORY_READ_ERROR,
    CODE_INVALID_HISTORY_ARTIFACT,
    CODE_SNAPSHOT_INTEGRITY_MISMATCH,
    CODE_UNSUPPORTED_HISTORY_SCHEMA,
    CODE_UNSUPPORTED_HISTORY_VERSION,
    DiagnosticError,
    HistoryError,
    map_comparison_error,
    map_snapshot_error,
)
from governance.history.models import (
    HISTORY_SCHEMA,
    HISTORY_VERSION,
    ComparisonPolicy,
    GovernanceHistory,
    HistoryEntry,
    HistoryEntryState,
    HistoryOperator,
    normalize_history_relative_path,
    normalize_labels,
    parse_content_identity,
    validate_captured_at,
    validate_operator_context_coupling,
)
from governance.identity.json_values import validate_json_value
from governance.snapshots.errors import SnapshotError
from governance.snapshots.load import load_snapshot_artifact
from governance.snapshots.models import GovernanceSnapshot

_SCHEMA_RESOURCE = "governance-history.v1.schema.json"
_validator: Draft202012Validator | None = None


@dataclass(frozen=True, slots=True)
class ResolvedEntryState:
    entry: HistoryEntry
    snapshot: GovernanceSnapshot
    observations: PropertyObservationSet | None
    authority: NormalizedAuthorityPolicySet | None
    conflicts: PropertyConflictReport | None
    projected: ProjectedSnapshot


@dataclass(frozen=True, slots=True)
class ResolvedHistory:
    history: GovernanceHistory
    states: tuple[ResolvedEntryState, ...]
    comparisons: tuple[dict[str, Any], ...]


def _load_schema() -> dict[str, Any]:
    text = (
        files("governance.history.schemas").joinpath(_SCHEMA_RESOURCE).read_text(encoding="utf-8")
    )
    return json.loads(text)


def _get_validator() -> Draft202012Validator:
    global _validator
    if _validator is None:
        _validator = Draft202012Validator(_load_schema())
    return _validator


def _schema_diagnostic(error: jsonschema.ValidationError) -> DiagnosticError:
    path_parts = [str(part) for part in error.absolute_path]
    logical_path = "/" + "/".join(path_parts) if path_parts else "/"
    return DiagnosticError(
        code=CODE_INVALID_HISTORY_ARTIFACT,
        path=logical_path,
        message=f"history artifact failed schema validation ({error.validator})",
    )


def _filesystem_path(history_path: Path, relative: str) -> Path:
    # Join without resolve — let loaders follow symlinks.
    try:
        return history_path.parent / Path(relative)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/",
                    message="unable to join referenced path",
                )
            ]
        ) from exc


def _parse_context(raw: Any, *, path: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="invalid history context",
                )
            ]
        )
    keys = set(raw)
    if keys == {"observations"}:
        observations = parse_content_identity(raw["observations"], path=f"{path}/observations")
        return {"observations": observations.to_dict()}
    if keys == {"observations", "authority", "conflicts"}:
        return {
            "authority": parse_content_identity(
                raw["authority"], path=f"{path}/authority"
            ).to_dict(),
            "conflicts": parse_content_identity(
                raw["conflicts"], path=f"{path}/conflicts"
            ).to_dict(),
            "observations": parse_content_identity(
                raw["observations"], path=f"{path}/observations"
            ).to_dict(),
        }
    raise HistoryError(
        [
            DiagnosticError(
                code=CODE_INVALID_HISTORY_ARTIFACT,
                path=path,
                message="invalid history context form",
            )
        ]
    )


def _parse_operator(raw: Any, *, path: str) -> HistoryOperator:
    if not isinstance(raw, dict):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="operator is required",
                )
            ]
        )
    if "conflicts_path" in raw:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=f"{path}/conflicts_path",
                    message="conflicts_path is not allowed",
                )
            ]
        )
    allowed = {"snapshot_path", "observations_path", "authority_paths"}
    if not set(raw) <= allowed or "snapshot_path" not in raw:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=path,
                    message="invalid history operator",
                )
            ]
        )
    snapshot_path = normalize_history_relative_path(
        raw["snapshot_path"], path=f"{path}/snapshot_path"
    )
    observations_path: str | None = None
    if "observations_path" in raw:
        observations_path = normalize_history_relative_path(
            raw["observations_path"], path=f"{path}/observations_path"
        )
    authority_paths: tuple[str, ...] | None = None
    if "authority_paths" in raw:
        raw_paths = raw["authority_paths"]
        if not isinstance(raw_paths, list) or not raw_paths:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path=f"{path}/authority_paths",
                        message="authority_paths must be a non-empty array",
                    )
                ]
            )
        authority_paths = tuple(
            normalize_history_relative_path(item, path=f"{path}/authority_paths/{index}")
            for index, item in enumerate(raw_paths)
        )
    return HistoryOperator(
        snapshot_path=snapshot_path,
        observations_path=observations_path,
        authority_paths=authority_paths,
    )


def _parse_entry(raw: Any, *, index: int) -> HistoryEntry:
    entry_path = f"/entries/{index}"
    if not isinstance(raw, dict):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=entry_path,
                    message="invalid history entry",
                )
            ]
        )
    state_raw = raw.get("state")
    if not isinstance(state_raw, dict):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=f"{entry_path}/state",
                    message="entry state is required",
                )
            ]
        )
    snapshot = parse_content_identity(
        state_raw.get("snapshot"),
        path=f"{entry_path}/state/snapshot",
    )
    labels = None
    if "labels" in state_raw:
        labels = normalize_labels(state_raw["labels"], path=f"{entry_path}/state/labels")
    context = None
    if "context" in state_raw:
        context = _parse_context(state_raw["context"], path=f"{entry_path}/state/context")

    unexpected_state = set(state_raw) - {"snapshot", "labels", "context"}
    if unexpected_state:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=f"{entry_path}/state",
                    message="invalid history entry state",
                )
            ]
        )

    state = HistoryEntryState(snapshot=snapshot, labels=labels, context=context)
    operator = _parse_operator(raw.get("operator"), path=f"{entry_path}/operator")
    validate_operator_context_coupling(state, operator, entry_path=entry_path)
    captured_at = None
    if "captured_at" in raw:
        captured_at = validate_captured_at(raw["captured_at"], path=f"{entry_path}/captured_at")
    unexpected_entry = set(raw) - {"state", "operator", "captured_at"}
    if unexpected_entry:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path=entry_path,
                    message="invalid history entry",
                )
            ]
        )
    return HistoryEntry(state=state, operator=operator, captured_at=captured_at)


def _parse_history_payload(payload: dict[str, Any]) -> GovernanceHistory:
    policy_raw = payload.get("comparison_policy")
    if not isinstance(policy_raw, dict):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/comparison_policy",
                    message="comparison_policy is required",
                )
            ]
        )
    if set(policy_raw) != {"align_source_roots", "align_database_roots"}:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/comparison_policy",
                    message="invalid comparison_policy",
                )
            ]
        )
    align_source = policy_raw["align_source_roots"]
    align_database = policy_raw["align_database_roots"]
    if not isinstance(align_source, bool) or not isinstance(align_database, bool):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/comparison_policy",
                    message="comparison_policy fields must be booleans",
                )
            ]
        )
    policy = ComparisonPolicy(
        align_source_roots=align_source,
        align_database_roots=align_database,
    )

    entries_raw = payload.get("entries")
    if not isinstance(entries_raw, list):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/entries",
                    message="entries must be an array",
                )
            ]
        )
    entries = tuple(_parse_entry(item, index=index) for index, item in enumerate(entries_raw))
    return GovernanceHistory(comparison_policy=policy, entries=entries)


def load_history_artifact(path: str | Path) -> GovernanceHistory:
    """Load, schema-validate, and verify a persisted governance-history v1 artifact."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_HISTORY_PARSE_ERROR,
                    path="/",
                    message="invalid history JSON",
                )
            ]
        ) from exc
    except OSError as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_HISTORY_READ_ERROR,
                    path="/",
                    message="unable to read history artifact",
                )
            ]
        ) from exc

    def _reject_non_standard_json(_value: str) -> None:
        raise ValueError("non-standard JSON literal")

    def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            text,
            parse_constant=_reject_non_standard_json,
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_HISTORY_PARSE_ERROR,
                    path="/",
                    message="invalid history JSON",
                )
            ]
        ) from exc

    try:
        validate_json_value(payload)
    except (TypeError, ValueError, RecursionError) as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_HISTORY_PARSE_ERROR,
                    path="/",
                    message="invalid history JSON",
                )
            ]
        ) from exc

    if not isinstance(payload, dict):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_HISTORY_PARSE_ERROR,
                    path="/",
                    message="history root must be a mapping",
                )
            ]
        )

    schema_name = payload.get("history_schema")
    version = payload.get("history_version")
    if schema_name != HISTORY_SCHEMA:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_UNSUPPORTED_HISTORY_SCHEMA,
                    path="/history_schema",
                    message="unsupported history schema",
                )
            ]
        )
    if version != HISTORY_VERSION:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_UNSUPPORTED_HISTORY_VERSION,
                    path="/history_version",
                    message="unsupported history version",
                )
            ]
        )

    try:
        schema_errors = sorted(
            _get_validator().iter_errors(payload),
            key=lambda item: (
                "/" + "/".join(str(part) for part in item.absolute_path),
                item.validator,
            ),
        )
    except RecursionError as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/",
                    message="history artifact is too deeply nested",
                )
            ]
        ) from exc
    if schema_errors:
        raise HistoryError([_schema_diagnostic(error) for error in schema_errors])

    identity_raw = payload.get("content_identity")
    if not isinstance(identity_raw, dict):
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/content_identity",
                    message="history content_identity is required",
                )
            ]
        )

    history = _parse_history_payload(payload)
    expected = history.content_identity()
    try:
        actual = parse_content_identity(identity_raw, path="/content_identity")
    except HistoryError as exc:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_HISTORY_INTEGRITY_MISMATCH,
                    path="/content_identity",
                    message="history content_identity mismatch",
                )
            ]
        ) from exc
    if actual != expected:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_HISTORY_INTEGRITY_MISMATCH,
                    path="/content_identity",
                    message="history content_identity mismatch",
                )
            ]
        )
    return history


def resolve_history_artifacts(
    history: GovernanceHistory,
    history_path: str | Path,
) -> ResolvedHistory:
    """Resolve every entry ref and validate all adjacent snapshot pairs."""
    target = Path(history_path)
    resolved_states: list[ResolvedEntryState] = []

    for index, entry in enumerate(history.entries):
        entry_path = f"/entries/{index}"
        snapshot_fs = _filesystem_path(target, entry.operator.snapshot_path)
        try:
            snapshot = load_snapshot_artifact(snapshot_fs)
        except SnapshotError as exc:
            raise HistoryError(
                [map_snapshot_error(exc, entry_path=f"{entry_path}/operator/snapshot_path")]
            ) from exc
        if snapshot.content_identity() != entry.state.snapshot:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_SNAPSHOT_INTEGRITY_MISMATCH,
                        path=f"{entry_path}/state/snapshot",
                        message="snapshot content_identity mismatch",
                    )
                ]
            )

        observations_fs = None
        if entry.operator.observations_path is not None:
            observations_fs = _filesystem_path(target, entry.operator.observations_path)
        authority_fs: list[Path] | None = None
        if entry.operator.authority_paths is not None:
            authority_fs = [
                _filesystem_path(target, item) for item in entry.operator.authority_paths
            ]

        context = resolve_entry_context(
            entry.state,
            observations_path=observations_fs,
            authority_paths=authority_fs,
            entry_path=entry_path,
        )

        try:
            projected = project_snapshot(snapshot, side="baseline")
        except ComparisonError as exc:
            raise HistoryError(map_comparison_error(exc, candidate_entry_index=index)) from exc

        resolved_states.append(
            ResolvedEntryState(
                entry=entry,
                snapshot=snapshot,
                observations=context.observations,
                authority=context.authority,
                conflicts=context.conflicts,
                projected=projected,
            )
        )

    comparisons: list[dict[str, Any]] = []
    ack = RootAlignmentAck(
        align_source_roots=history.comparison_policy.align_source_roots,
        align_database_roots=history.comparison_policy.align_database_roots,
    )
    for index in range(len(resolved_states) - 1):
        left = resolved_states[index]
        right = resolved_states[index + 1]
        try:
            comparison = build_comparison_result(left.snapshot, right.snapshot, ack=ack)
        except ComparisonError as exc:
            raise HistoryError(map_comparison_error(exc, candidate_entry_index=index + 1)) from exc
        comparisons.append(comparison)

    return ResolvedHistory(
        history=history,
        states=tuple(resolved_states),
        comparisons=tuple(comparisons),
    )


__all__ = [
    "ResolvedEntryState",
    "ResolvedHistory",
    "load_history_artifact",
    "resolve_history_artifacts",
]
