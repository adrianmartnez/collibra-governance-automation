"""Explicit reconciliation source composition (no graph merge, no cwd discovery)."""

from __future__ import annotations

from dataclasses import dataclass

from governance.domain.graph import GraphNodeIdentity
from governance.domain.observations import PropertyObservationSet
from governance.providers.registry import ProviderRegistry
from governance.reconciliation.errors import ReconciliationError


@dataclass(frozen=True, slots=True)
class ReconciliationSourceBundle:
    observations: PropertyObservationSet
    known_objects: tuple[GraphNodeIdentity, ...]


def compose_reconciliation_sources(
    *,
    namespace: str,
    odcs_paths: list[str] | tuple[str, ...] | None = None,
    dbt_paths: list[str] | tuple[str, ...] | None = None,
    openlineage_paths: list[str] | tuple[str, ...] | None = None,
    dbt_default_database: str | None = None,
    registry: ProviderRegistry | None = None,
) -> ReconciliationSourceBundle:
    """Load mapper-time observations + known object identities via built-in providers.

    Paths within each kind are sorted lexicographically for deterministic
    diagnostic indexes. Duplicate CLI paths are preserved as separate load slots
    before observation merge may dedupe semantics.
    """
    # Lazy imports avoid providers.builtins ↔ plans ↔ reconciliation cycles.
    from governance.orchestration.registry import build_provider_registry
    from governance.orchestration.sources import (
        compose_legacy_reconciliation_jobs,
        run_reconciliation_sources,
    )

    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("namespace must be a non-empty string")
    ns = namespace.strip()

    odcs = sorted(str(path) for path in (odcs_paths or ()))
    dbt = sorted(str(path) for path in (dbt_paths or ()))
    openlineage = sorted(str(path) for path in (openlineage_paths or ()))

    if registry is None:
        registry = build_provider_registry(discover_external=True)
    jobs = compose_legacy_reconciliation_jobs(
        namespace=ns,
        odcs_paths=odcs,
        dbt_paths=dbt,
        openlineage_paths=openlineage,
        dbt_default_database=dbt_default_database,
        registry=registry,
    )
    try:
        result = run_reconciliation_sources(jobs)
    except ReconciliationError:
        raise
    return ReconciliationSourceBundle(
        observations=result.observations,
        known_objects=result.known_objects,
    )


def has_reconciliation_source_flags(
    *,
    odcs_paths: list[str] | tuple[str, ...] | None = None,
    dbt_paths: list[str] | tuple[str, ...] | None = None,
    openlineage_paths: list[str] | tuple[str, ...] | None = None,
) -> bool:
    return bool(odcs_paths or dbt_paths or openlineage_paths)
