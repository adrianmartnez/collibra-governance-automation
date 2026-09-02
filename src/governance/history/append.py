"""Append a new entry to a governance-history artifact."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from governance.comparison.align import RootAlignmentAck
from governance.comparison.errors import ComparisonError
from governance.comparison.result import build_comparison_result
from governance.history.context import (
    build_context_identities,
    load_authority_at,
    load_observations_at,
)
from governance.history.errors import (
    CODE_DUPLICATE_HISTORY_STATE,
    CODE_INVALID_HISTORY_ARTIFACT,
    DiagnosticError,
    HistoryError,
    map_comparison_error,
    map_snapshot_error,
)
from governance.history.load import load_history_artifact, resolve_history_artifacts
from governance.history.models import (
    ComparisonPolicy,
    GovernanceHistory,
    HistoryEntry,
    HistoryEntryState,
    HistoryOperator,
    normalize_history_relative_path,
    normalize_labels,
    validate_captured_at,
    validate_operator_context_coupling,
)
from governance.history.serialize import write_history_artifact
from governance.identity.hashing import history_entry_state_identity
from governance.snapshots.errors import SnapshotError
from governance.snapshots.load import load_snapshot_artifact


def _exact_optional_bool(value: object, *, path: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise HistoryError(
        [
            DiagnosticError(
                code=CODE_INVALID_HISTORY_ARTIFACT,
                path=path,
                message="alignment flag must be a boolean or omitted",
            )
        ]
    )


def append_history_entry(
    history_path: str | Path,
    *,
    snapshot_path: str,
    observations_path: str | None = None,
    authority_paths: Sequence[str] | None = None,
    labels: Mapping[str, str] | None = None,
    captured_at: str | None = None,
    align_source_roots: bool | None = None,
    align_database_roots: bool | None = None,
) -> GovernanceHistory:
    """Append one history entry (ADD flow); atomically write only ``history_path``."""
    target = Path(history_path)
    snapshot_rel = normalize_history_relative_path(snapshot_path, path="/operator/snapshot_path")
    observations_rel = None
    if observations_path is not None:
        observations_rel = normalize_history_relative_path(
            observations_path, path="/operator/observations_path"
        )
    authority_rels: tuple[str, ...] | None = None
    if authority_paths is not None:
        if isinstance(authority_paths, (str, bytes)):
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path="/operator/authority_paths",
                        message="authority_paths must be a sequence of path strings",
                    )
                ]
            )
        if not authority_paths:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path="/operator/authority_paths",
                        message="authority_paths must be non-empty when provided",
                    )
                ]
            )
        for index, item in enumerate(authority_paths):
            if not isinstance(item, str):
                raise HistoryError(
                    [
                        DiagnosticError(
                            code=CODE_INVALID_HISTORY_ARTIFACT,
                            path=f"/operator/authority_paths/{index}",
                            message="authority_paths must be a sequence of path strings",
                        )
                    ]
                )
        authority_rels = tuple(
            normalize_history_relative_path(item, path=f"/operator/authority_paths/{index}")
            for index, item in enumerate(authority_paths)
        )

    if authority_rels is not None and observations_rel is None:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_HISTORY_ARTIFACT,
                    path="/context",
                    message="authority requires observations",
                )
            ]
        )

    normalized_labels = normalize_labels(labels, path="/labels")
    validated_captured_at = validate_captured_at(captured_at, path="/captured_at")

    snapshot_fs = target.parent / Path(snapshot_rel)
    try:
        snapshot = load_snapshot_artifact(snapshot_fs)
    except SnapshotError as exc:
        raise HistoryError([map_snapshot_error(exc, entry_path="/")]) from exc

    align_source = _exact_optional_bool(
        align_source_roots, path="/comparison_policy/align_source_roots"
    )
    align_database = _exact_optional_bool(
        align_database_roots, path="/comparison_policy/align_database_roots"
    )

    existing: GovernanceHistory | None = None
    resolved_existing = None
    if target.is_file():
        existing = load_history_artifact(target)
        resolved_existing = resolve_history_artifacts(existing, target)
        policy = existing.comparison_policy
        if align_source is not None and align_source != policy.align_source_roots:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path="/comparison_policy/align_source_roots",
                        message="align_source_roots conflicts with stored comparison_policy",
                    )
                ]
            )
        if align_database is not None and align_database != policy.align_database_roots:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_INVALID_HISTORY_ARTIFACT,
                        path="/comparison_policy/align_database_roots",
                        message="align_database_roots conflicts with stored comparison_policy",
                    )
                ]
            )
    else:
        policy = ComparisonPolicy(
            align_source_roots=False if align_source is None else align_source,
            align_database_roots=False if align_database is None else align_database,
        )

    observations = None
    authority = None
    if observations_rel is not None:
        observations = load_observations_at(
            target.parent / Path(observations_rel),
            entry_path="/",
        )
    if authority_rels is not None:
        authority = load_authority_at(
            [target.parent / Path(item) for item in authority_rels],
            entry_path="/",
        )

    context = build_context_identities(observations=observations, authority=authority)
    state = HistoryEntryState(
        snapshot=snapshot.content_identity(),
        labels=normalized_labels,
        context=context,
    )
    operator = HistoryOperator(
        snapshot_path=snapshot_rel,
        observations_path=observations_rel,
        authority_paths=authority_rels,
    )
    validate_operator_context_coupling(state, operator, entry_path="/")

    if existing is not None and resolved_existing is not None and existing.entries:
        last = resolved_existing.states[-1]
        ack = RootAlignmentAck(
            align_source_roots=policy.align_source_roots,
            align_database_roots=policy.align_database_roots,
        )
        try:
            build_comparison_result(last.snapshot, snapshot, ack=ack)
        except ComparisonError as exc:
            raise HistoryError(
                map_comparison_error(exc, candidate_entry_index=len(existing.entries))
            ) from exc

        last_identity = history_entry_state_identity(last.entry.state.to_identity_dict())
        new_identity = history_entry_state_identity(state.to_identity_dict())
        if last_identity == new_identity:
            raise HistoryError(
                [
                    DiagnosticError(
                        code=CODE_DUPLICATE_HISTORY_STATE,
                        path=f"/entries/{len(existing.entries)}",
                        message="adjacent history entry state is duplicate",
                    )
                ]
            )

    new_entry = HistoryEntry(
        state=state,
        operator=operator,
        captured_at=validated_captured_at,
    )
    if existing is None:
        updated = GovernanceHistory(comparison_policy=policy, entries=(new_entry,))
    else:
        updated = GovernanceHistory(
            comparison_policy=policy,
            entries=(*existing.entries, new_entry),
        )

    # Semantic validate via round-trip identity + operator coupling already checked
    _ = updated.content_identity()
    write_history_artifact(updated, target)
    return updated


__all__ = ["append_history_entry"]
