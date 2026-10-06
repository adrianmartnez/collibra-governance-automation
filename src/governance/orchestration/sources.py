"""Provider-driven source execution and reconciliation composition."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from governance.config_contract.provider_resolution import (
    ResolvedProviderBinding,
    construct_provider_capability,
)
from governance.domain.graph import GovernanceGraph, GraphNodeIdentity
from governance.domain.models import GovernanceModel
from governance.domain.observations import PropertyObservationSet
from governance.providers import CapabilityId, ProviderRegistry
from governance.providers.contracts import (
    GovernanceGraphCapability,
    MetadataDiscoveryCapability,
    PropertyObservationsCapability,
    ProviderRegistration,
    ProviderRuntimeContext,
)
from governance.reconciliation.errors import (
    CODE_SOURCE_ERROR,
    DiagnosticError,
    ReconciliationError,
)


@dataclass(frozen=True, slots=True)
class SourceCapabilityJob:
    logical_id: str
    provider_id: str
    binding: ResolvedProviderBinding
    capabilities: frozenset[CapabilityId]
    diagnostic_path: str = ""


@dataclass(frozen=True, slots=True)
class SourceExecutionResult:
    observations: PropertyObservationSet
    known_objects: tuple[GraphNodeIdentity, ...]


def run_metadata_discovery(binding: ResolvedProviderBinding) -> GovernanceModel:
    """Construct metadata_discovery and invoke ``discover()``."""
    capability = cast(
        MetadataDiscoveryCapability,
        construct_provider_capability(binding, CapabilityId.METADATA_DISCOVERY),
    )
    return capability.discover()


def run_reconciliation_sources(jobs: Sequence[SourceCapabilityJob]) -> SourceExecutionResult:
    """Execute property_observations (+ graph nodes) for each job; merge in core."""
    observation_sets: list[PropertyObservationSet] = []
    identities: list[GraphNodeIdentity] = []

    for job in jobs:
        path = job.diagnostic_path or f"/sources/{job.logical_id}"
        try:
            if CapabilityId.PROPERTY_OBSERVATIONS not in job.capabilities:
                raise ReconciliationError(
                    [
                        DiagnosticError(
                            code=CODE_SOURCE_ERROR,
                            path=path,
                            message="source provider missing property_observations capability",
                        )
                    ]
                )
            obs_cap = cast(
                PropertyObservationsCapability,
                construct_provider_capability(job.binding, CapabilityId.PROPERTY_OBSERVATIONS),
            )
            observations = obs_cap.load_observations()
            if not isinstance(observations, PropertyObservationSet):
                raise TypeError("load_observations must return PropertyObservationSet")
            observation_sets.append(observations)

            if CapabilityId.GOVERNANCE_GRAPH in job.capabilities:
                graph_cap = cast(
                    GovernanceGraphCapability,
                    construct_provider_capability(job.binding, CapabilityId.GOVERNANCE_GRAPH),
                )
                graph = graph_cap.load_graph()
                if not isinstance(graph, GovernanceGraph):
                    raise TypeError("load_graph must return GovernanceGraph")
                for node in graph.nodes:
                    identities.append(node.identity)
        except ReconciliationError:
            raise
        except Exception as exc:
            from governance.integrations.dbt import DbtError
            from governance.integrations.odcs import OdcsError
            from governance.integrations.openlineage import OpenLineageError

            if isinstance(exc, (OdcsError, DbtError, OpenLineageError)):
                raise ReconciliationError(
                    [
                        DiagnosticError(
                            code=CODE_SOURCE_ERROR,
                            path=getattr(item, "path", "") or path,
                            message=item.message,
                        )
                        for item in exc.errors
                    ]
                ) from exc
            raise ReconciliationError(
                [
                    DiagnosticError(
                        code=CODE_SOURCE_ERROR,
                        path=path,
                        message=f"{job.provider_id} source could not be loaded",
                    )
                ]
            ) from exc

    if not observation_sets:
        observations = PropertyObservationSet()
    else:
        observations = PropertyObservationSet.merge(*observation_sets)

    return SourceExecutionResult(
        observations=observations,
        known_objects=_sorted_unique_identities(identities),
    )


def run_impact_graph(jobs: Sequence[SourceCapabilityJob]) -> GovernanceGraph:
    """Load and merge governance graphs for impact analysis."""
    graphs: list[GovernanceGraph] = []
    for job in jobs:
        path = job.diagnostic_path or f"/sources/{job.logical_id}"
        try:
            graph_cap = cast(
                GovernanceGraphCapability,
                construct_provider_capability(job.binding, CapabilityId.GOVERNANCE_GRAPH),
            )
            graph = graph_cap.load_graph()
            if not isinstance(graph, GovernanceGraph):
                raise TypeError("load_graph must return GovernanceGraph")
            graphs.append(graph)
        except ReconciliationError:
            raise
        except Exception as exc:
            from governance.integrations.dbt import DbtError
            from governance.integrations.odcs import OdcsError
            from governance.integrations.openlineage import OpenLineageError

            if isinstance(exc, (OdcsError, DbtError, OpenLineageError)):
                raise ReconciliationError(
                    [
                        DiagnosticError(
                            code=CODE_SOURCE_ERROR,
                            path=getattr(item, "path", "") or "",
                            message=item.message,
                        )
                        for item in exc.errors
                    ]
                ) from exc
            raise ReconciliationError(
                [
                    DiagnosticError(
                        code=CODE_SOURCE_ERROR,
                        path=path,
                        message=f"{job.provider_id} source could not be loaded",
                    )
                ]
            ) from exc
    if not graphs:
        return GovernanceGraph.from_parts((), ())
    return GovernanceGraph.from_parts(
        [node for graph in graphs for node in graph.nodes],
        [edge for graph in graphs for edge in graph.edges],
    )


def _sorted_unique_identities(
    identities: list[GraphNodeIdentity],
) -> tuple[GraphNodeIdentity, ...]:
    unique: dict[bytes, GraphNodeIdentity] = {}
    for identity in identities:
        unique[identity.canonical_bytes()] = identity
    return tuple(sorted(unique.values(), key=lambda item: item.canonical_bytes()))


def legacy_file_source_binding(
    *,
    provider_id: str,
    path: str,
    namespace: str,
    dbt_default_database: str | None = None,
    registration: ProviderRegistration,
) -> ResolvedProviderBinding:
    """Build a ResolvedProviderBinding for legacy CLI absolute/relative paths.

    Paths keep caller-supplied semantics (cwd-relative as today); config_root is
    None so factories resolve ``path`` as given when absolute, or as-is string.
    """
    config: dict[str, object] = {"path": path, "namespace": namespace}
    if provider_id == "dbt" and dbt_default_database is not None:
        config["default_database"] = dbt_default_database
    # Legacy paths are often absolute or cwd-relative; pass as path string and
    # set config_root empty so builtins join carefully — see factory path resolve.
    runtime = ProviderRuntimeContext(config=config, config_root=None)
    return ResolvedProviderBinding(
        role="source",
        logical_id=provider_id,
        provider_id=provider_id,
        registration=registration,
        runtime_context=runtime,
        config_path=f"/legacy/{provider_id}",
    )


def compose_legacy_reconciliation_jobs(
    *,
    namespace: str,
    odcs_paths: Sequence[str],
    dbt_paths: Sequence[str],
    openlineage_paths: Sequence[str],
    dbt_default_database: str | None,
    registry: ProviderRegistry,
) -> list[SourceCapabilityJob]:
    """Translate legacy CLI flags into provider-driven reconciliation jobs."""
    jobs: list[SourceCapabilityJob] = []
    required = frozenset({CapabilityId.GOVERNANCE_GRAPH, CapabilityId.PROPERTY_OBSERVATIONS})

    def _append(kind: str, paths: Sequence[str], provider_id: str) -> None:
        registration = registry.require_capabilities(provider_id, list(required))
        for index, path in enumerate(paths):
            binding = legacy_file_source_binding(
                provider_id=provider_id,
                path=path,
                namespace=namespace,
                dbt_default_database=dbt_default_database if provider_id == "dbt" else None,
                registration=registration,
            )
            jobs.append(
                SourceCapabilityJob(
                    logical_id=f"{kind}-{index}",
                    provider_id=provider_id,
                    binding=binding,
                    capabilities=required,
                    diagnostic_path=f"/sources/{kind}/{index}",
                )
            )

    _append("odcs", odcs_paths, "odcs")
    _append("dbt", dbt_paths, "dbt")
    _append("openlineage", openlineage_paths, "openlineage")
    return jobs


def compose_legacy_impact_jobs(
    *,
    namespace: str,
    odcs_paths: Sequence[str],
    dbt_paths: Sequence[str],
    openlineage_paths: Sequence[str],
    dbt_default_database: str | None,
    registry: ProviderRegistry,
) -> list[SourceCapabilityJob]:
    """Legacy CLI impact sources: governance_graph only, deterministic sort order."""
    specs: list[tuple[str, str, str]] = []
    specs.extend(("dbt", path, "dbt") for path in dbt_paths)
    specs.extend(("odcs", path, "odcs") for path in odcs_paths)
    specs.extend(("openlineage", path, "openlineage") for path in openlineage_paths)
    specs.sort(key=lambda item: (item[0], item[1]))

    required = frozenset({CapabilityId.GOVERNANCE_GRAPH})
    jobs: list[SourceCapabilityJob] = []
    kind_indexes: dict[str, int] = {}
    for kind, path, provider_id in specs:
        registration = registry.require_capabilities(provider_id, list(required))
        binding = legacy_file_source_binding(
            provider_id=provider_id,
            path=path,
            namespace=namespace,
            dbt_default_database=dbt_default_database if provider_id == "dbt" else None,
            registration=registration,
        )
        index = kind_indexes.get(kind, 0)
        kind_indexes[kind] = index + 1
        jobs.append(
            SourceCapabilityJob(
                logical_id=f"{kind}-{index}",
                provider_id=provider_id,
                binding=binding,
                capabilities=required,
                diagnostic_path=f"/sources/{kind}/{index}",
            )
        )
    return jobs
