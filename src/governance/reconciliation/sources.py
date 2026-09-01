"""Explicit reconciliation source composition (no graph merge, no cwd discovery)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from governance.domain.graph import GraphNodeIdentity
from governance.domain.observations import PropertyObservationSet
from governance.integrations.dbt import load_dbt_graph_with_observations
from governance.integrations.odcs import load_odcs_graph_with_observations
from governance.integrations.openlineage import load_openlineage_graph_with_observations
from governance.reconciliation.errors import (
    CODE_SOURCE_ERROR,
    DiagnosticError,
    ReconciliationError,
)


@dataclass(frozen=True, slots=True)
class ReconciliationSourceBundle:
    observations: PropertyObservationSet
    known_objects: tuple[GraphNodeIdentity, ...]


def _sorted_unique_identities(
    identities: list[GraphNodeIdentity],
) -> tuple[GraphNodeIdentity, ...]:
    unique: dict[bytes, GraphNodeIdentity] = {}
    for identity in identities:
        unique[identity.canonical_bytes()] = identity
    return tuple(sorted(unique.values(), key=lambda item: item.canonical_bytes()))


def compose_reconciliation_sources(
    *,
    namespace: str,
    odcs_paths: list[str] | tuple[str, ...] | None = None,
    dbt_paths: list[str] | tuple[str, ...] | None = None,
    openlineage_paths: list[str] | tuple[str, ...] | None = None,
    dbt_default_database: str | None = None,
) -> ReconciliationSourceBundle:
    """Load mapper-time observations + known object identities.

    Paths within each kind are sorted lexicographically for deterministic
    diagnostic indexes. Duplicate CLI paths are preserved as separate load slots
    before observation merge may dedupe semantics.
    """
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("namespace must be a non-empty string")
    ns = namespace.strip()

    odcs = sorted(str(path) for path in (odcs_paths or ()))
    dbt = sorted(str(path) for path in (dbt_paths or ()))
    openlineage = sorted(str(path) for path in (openlineage_paths or ()))

    observation_sets: list[PropertyObservationSet] = []
    identities: list[GraphNodeIdentity] = []

    def _load(kind: str, index: int, path: str, loader) -> None:
        try:
            result = loader()
        except Exception as exc:
            raise ReconciliationError(
                [
                    DiagnosticError(
                        code=CODE_SOURCE_ERROR,
                        path=f"/sources/{kind}/{index}",
                        message=f"{kind} source could not be loaded",
                    )
                ]
            ) from exc
        observation_sets.append(result.observations)
        for node in result.graph.nodes:
            identities.append(node.identity)

    for index, path in enumerate(odcs):
        _load(
            "odcs",
            index,
            path,
            lambda p=path: load_odcs_graph_with_observations(Path(p), namespace=ns),
        )
    for index, path in enumerate(dbt):
        _load(
            "dbt",
            index,
            path,
            lambda p=path: load_dbt_graph_with_observations(
                Path(p),
                namespace=ns,
                default_database=dbt_default_database,
            ),
        )
    for index, path in enumerate(openlineage):
        _load(
            "openlineage",
            index,
            path,
            lambda p=path: load_openlineage_graph_with_observations(Path(p), namespace=ns),
        )

    if not observation_sets:
        observations = PropertyObservationSet()
    else:
        observations = PropertyObservationSet.merge(*observation_sets)

    return ReconciliationSourceBundle(
        observations=observations,
        known_objects=_sorted_unique_identities(identities),
    )


def has_reconciliation_source_flags(
    *,
    odcs_paths: list[str] | tuple[str, ...] | None = None,
    dbt_paths: list[str] | tuple[str, ...] | None = None,
    openlineage_paths: list[str] | tuple[str, ...] | None = None,
) -> bool:
    return bool(odcs_paths or dbt_paths or openlineage_paths)
