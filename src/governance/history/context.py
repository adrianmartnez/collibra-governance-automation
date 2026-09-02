"""Resolve optional history context (observations + authority → derived conflicts)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from governance.authority.errors import AuthorityError
from governance.authority.load import load_normalized_authority_files
from governance.domain.authority import NormalizedAuthorityPolicySet
from governance.domain.conflicts import PropertyConflictReport, analyze_property_conflicts
from governance.domain.observations import PropertyObservationSet
from governance.history.errors import (
    CODE_CONTEXT_INTEGRITY_MISMATCH,
    CODE_INVALID_CONTEXT_ARTIFACT,
    DiagnosticError,
    HistoryError,
    map_authority_error,
    map_observations_error,
)
from governance.history.models import HistoryEntryState, parse_content_identity
from governance.identity.hashing import ContentIdentity
from governance.observations.artifact import (
    ObservationsArtifactError,
    load_property_observation_set_artifact,
)


@dataclass(frozen=True, slots=True)
class ResolvedContext:
    observations: PropertyObservationSet | None
    authority: NormalizedAuthorityPolicySet | None
    conflicts: PropertyConflictReport | None
    context_identity: dict[str, Any] | None


def build_context_identities(
    *,
    observations: PropertyObservationSet | None = None,
    authority: NormalizedAuthorityPolicySet | None = None,
) -> dict[str, Any] | None:
    """Build canonical context identity block (derive conflicts when full)."""
    if observations is None and authority is None:
        return None
    if observations is None:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_CONTEXT_ARTIFACT,
                    path="/context",
                    message="authority requires observations",
                )
            ]
        )
    if authority is None:
        return {"observations": observations.content_identity().to_dict()}

    report = analyze_property_conflicts(observations, authority)
    return {
        "authority": authority.content_identity().to_dict(),
        "conflicts": report.content_identity().to_dict(),
        "observations": observations.content_identity().to_dict(),
    }


def load_observations_at(
    path: Path,
    *,
    expected: ContentIdentity | None = None,
    entry_path: str,
) -> PropertyObservationSet:
    try:
        observation_set = load_property_observation_set_artifact(path)
    except ObservationsArtifactError as exc:
        raise HistoryError(map_observations_error(exc, entry_path=entry_path)) from exc
    if expected is not None and observation_set.content_identity() != expected:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_CONTEXT_INTEGRITY_MISMATCH,
                    path=f"{entry_path}/context/observations",
                    message="observations content_identity mismatch",
                )
            ]
        )
    return observation_set


def load_authority_at(
    paths: Sequence[Path],
    *,
    expected: ContentIdentity | None = None,
    entry_path: str,
) -> NormalizedAuthorityPolicySet:
    try:
        authority = load_normalized_authority_files(paths)
    except AuthorityError as exc:
        raise HistoryError(map_authority_error(exc, entry_path=entry_path)) from exc
    if expected is not None and authority.content_identity() != expected:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_CONTEXT_INTEGRITY_MISMATCH,
                    path=f"{entry_path}/context/authority",
                    message="authority content_identity mismatch",
                )
            ]
        )
    return authority


def resolve_entry_context(
    state: HistoryEntryState,
    *,
    observations_path: Path | None,
    authority_paths: Sequence[Path] | None,
    entry_path: str,
) -> ResolvedContext:
    """Load and verify context for one history entry; recompute conflicts for FULL."""
    form = state.context_form()
    if form == "none":
        return ResolvedContext(
            observations=None,
            authority=None,
            conflicts=None,
            context_identity=None,
        )

    assert state.context is not None
    expected_observations = parse_content_identity(
        state.context["observations"],
        path=f"{entry_path}/state/context/observations",
    )
    if observations_path is None:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_CONTEXT_ARTIFACT,
                    path=f"{entry_path}/operator/observations_path",
                    message="observations_path is required for contextual entries",
                )
            ]
        )

    observations = load_observations_at(
        observations_path,
        expected=expected_observations,
        entry_path=entry_path,
    )

    if form == "provenance":
        return ResolvedContext(
            observations=observations,
            authority=None,
            conflicts=None,
            context_identity={"observations": expected_observations.to_dict()},
        )

    expected_authority = parse_content_identity(
        state.context["authority"],
        path=f"{entry_path}/state/context/authority",
    )
    expected_conflicts = parse_content_identity(
        state.context["conflicts"],
        path=f"{entry_path}/state/context/conflicts",
    )
    if not authority_paths:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_INVALID_CONTEXT_ARTIFACT,
                    path=f"{entry_path}/operator/authority_paths",
                    message="authority_paths are required for full-context entries",
                )
            ]
        )

    authority = load_authority_at(
        authority_paths,
        expected=expected_authority,
        entry_path=entry_path,
    )
    report = analyze_property_conflicts(observations, authority)
    actual_conflicts = report.content_identity()
    if actual_conflicts != expected_conflicts:
        raise HistoryError(
            [
                DiagnosticError(
                    code=CODE_CONTEXT_INTEGRITY_MISMATCH,
                    path=f"{entry_path}/state/context/conflicts",
                    message="derived conflicts content_identity mismatch",
                )
            ]
        )
    return ResolvedContext(
        observations=observations,
        authority=authority,
        conflicts=report,
        context_identity={
            "authority": expected_authority.to_dict(),
            "conflicts": expected_conflicts.to_dict(),
            "observations": expected_observations.to_dict(),
        },
    )


__all__ = [
    "ResolvedContext",
    "build_context_identities",
    "load_authority_at",
    "load_observations_at",
    "resolve_entry_context",
]
