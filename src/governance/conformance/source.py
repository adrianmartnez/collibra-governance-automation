"""Source capability conformance suites (partial; not full provider conformance)."""

from __future__ import annotations

from collections.abc import Sequence

from governance.conformance.cases import (
    GraphCase,
    LineageCase,
    MetadataDiscoveryCase,
    ObservationsCase,
)
from governance.conformance.report import (
    ConformanceCheckResult,
    ConformanceReport,
    check,
    merge_reports,
)
from governance.domain.graph import GovernanceGraph
from governance.domain.lineage import ColumnLineageAssertion
from governance.domain.models import GovernanceModel
from governance.domain.observations import PropertyObservationSet
from governance.providers.capabilities import CapabilityId
from governance.providers.contracts import ProviderRegistration


def _construct(
    registration: ProviderRegistration, capability: CapabilityId, context: object
) -> object:
    binding = registration.binding_for(capability)
    return binding.factory(context)  # type: ignore[arg-type]


def _counter_zero(counter: object, *, check_id: str, path: str) -> ConformanceCheckResult:
    if counter is None:
        return check(
            id=check_id,
            passed=True,
            message="no mutation counter supplied; skipped measurable mutation assertion",
            path=path,
        )
    if not callable(counter):
        return check(
            id=check_id,
            passed=False,
            message="mutation_counter must be callable when provided",
            path=path,
        )
    value = int(counter())
    return check(
        id=check_id,
        passed=value == 0,
        message=(
            "measurable mutation counter remains 0"
            if value == 0
            else f"measurable mutation counter is {value}, expected 0"
        ),
        path=path,
    )


def run_metadata_discovery_conformance(case: MetadataDiscoveryCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    capability = CapabilityId.METADATA_DISCOVERY
    try:
        instance = _construct(case.registration, capability, case.context)
    except Exception as exc:  # noqa: BLE001
        return ConformanceReport(
            results=(
                check(
                    id="metadata_discovery_factory",
                    passed=False,
                    message=f"factory raised {type(exc).__name__}",
                    path="/capabilities/metadata_discovery/factory",
                ),
            )
        )
    results.append(
        check(
            id="metadata_discovery_factory",
            passed=True,
            message="factory constructed metadata_discovery capability",
            path="/capabilities/metadata_discovery/factory",
        )
    )

    discover = getattr(instance, "discover", None)
    if not callable(discover):
        results.append(
            check(
                id="metadata_discovery_operation",
                passed=False,
                message="capability must implement discover()",
                path="/capabilities/metadata_discovery/discover",
            )
        )
        return ConformanceReport(results=tuple(results))

    try:
        first = discover()
        second = discover()
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="metadata_discovery_operation",
                passed=False,
                message=f"discover() raised {type(exc).__name__}",
                path="/capabilities/metadata_discovery/discover",
            )
        )
        return ConformanceReport(results=tuple(results))

    results.append(
        check(
            id="metadata_discovery_operation",
            passed=isinstance(first, GovernanceModel),
            message=(
                "discover() returned GovernanceModel"
                if isinstance(first, GovernanceModel)
                else f"discover() returned {type(first).__name__}, expected GovernanceModel"
            ),
            path="/capabilities/metadata_discovery/discover",
        )
    )
    if not isinstance(first, GovernanceModel) or not isinstance(second, GovernanceModel):
        return ConformanceReport(results=tuple(results))

    if case.project is not None:
        left = case.project(first)
        right = case.project(second)
    else:
        left = first.to_json()
        right = second.to_json()
    results.append(
        check(
            id="metadata_discovery_deterministic",
            passed=left == right,
            message="repeated discover() yields equivalent canonical projection",
            path="/capabilities/metadata_discovery/discover",
        )
    )
    results.append(
        _counter_zero(
            case.mutation_counter,
            check_id="metadata_discovery_read_only",
            path="/capabilities/metadata_discovery",
        )
    )
    return ConformanceReport(results=tuple(results))


def run_graph_conformance(case: GraphCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    capability = CapabilityId.GOVERNANCE_GRAPH
    try:
        instance = _construct(case.registration, capability, case.context)
    except Exception as exc:  # noqa: BLE001
        return ConformanceReport(
            results=(
                check(
                    id="governance_graph_factory",
                    passed=False,
                    message=f"factory raised {type(exc).__name__}",
                    path="/capabilities/governance_graph/factory",
                ),
            )
        )
    results.append(
        check(
            id="governance_graph_factory",
            passed=True,
            message="factory constructed governance_graph capability",
            path="/capabilities/governance_graph/factory",
        )
    )
    load_graph = getattr(instance, "load_graph", None)
    if not callable(load_graph):
        results.append(
            check(
                id="governance_graph_operation",
                passed=False,
                message="capability must implement load_graph()",
                path="/capabilities/governance_graph/load_graph",
            )
        )
        return ConformanceReport(results=tuple(results))

    try:
        first = load_graph()
        second = load_graph()
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="governance_graph_operation",
                passed=False,
                message=f"load_graph() raised {type(exc).__name__}",
                path="/capabilities/governance_graph/load_graph",
            )
        )
        return ConformanceReport(results=tuple(results))

    ok_type = isinstance(first, GovernanceGraph)
    results.append(
        check(
            id="governance_graph_operation",
            passed=ok_type,
            message=(
                "load_graph() returned GovernanceGraph"
                if ok_type
                else f"load_graph() returned {type(first).__name__}, expected GovernanceGraph"
            ),
            path="/capabilities/governance_graph/load_graph",
        )
    )
    if not isinstance(first, GovernanceGraph) or not isinstance(second, GovernanceGraph):
        return ConformanceReport(results=tuple(results))

    results.append(
        check(
            id="governance_graph_deterministic",
            passed=first.content_identity() == second.content_identity(),
            message="repeated load_graph() yields equivalent content identity",
            path="/capabilities/governance_graph/load_graph",
        )
    )
    results.append(
        check(
            id="governance_graph_canonical",
            passed=first.canonical_dict_without_identity()
            == second.canonical_dict_without_identity(),
            message="repeated load_graph() yields equivalent canonical dict",
            path="/capabilities/governance_graph/load_graph",
        )
    )
    return ConformanceReport(results=tuple(results))


def run_observations_conformance(case: ObservationsCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    capability = CapabilityId.PROPERTY_OBSERVATIONS
    try:
        instance = _construct(case.registration, capability, case.context)
    except Exception as exc:  # noqa: BLE001
        return ConformanceReport(
            results=(
                check(
                    id="property_observations_factory",
                    passed=False,
                    message=f"factory raised {type(exc).__name__}",
                    path="/capabilities/property_observations/factory",
                ),
            )
        )
    results.append(
        check(
            id="property_observations_factory",
            passed=True,
            message="factory constructed property_observations capability",
            path="/capabilities/property_observations/factory",
        )
    )
    load_observations = getattr(instance, "load_observations", None)
    if not callable(load_observations):
        results.append(
            check(
                id="property_observations_operation",
                passed=False,
                message="capability must implement load_observations()",
                path="/capabilities/property_observations/load_observations",
            )
        )
        return ConformanceReport(results=tuple(results))

    try:
        first = load_observations()
        second = load_observations()
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="property_observations_operation",
                passed=False,
                message=f"load_observations() raised {type(exc).__name__}",
                path="/capabilities/property_observations/load_observations",
            )
        )
        return ConformanceReport(results=tuple(results))

    ok_type = isinstance(first, PropertyObservationSet)
    results.append(
        check(
            id="property_observations_operation",
            passed=ok_type,
            message=(
                "load_observations() returned PropertyObservationSet"
                if ok_type
                else (
                    f"load_observations() returned {type(first).__name__}, "
                    "expected PropertyObservationSet"
                )
            ),
            path="/capabilities/property_observations/load_observations",
        )
    )
    if not isinstance(first, PropertyObservationSet) or not isinstance(
        second, PropertyObservationSet
    ):
        return ConformanceReport(results=tuple(results))

    results.append(
        check(
            id="property_observations_deterministic",
            passed=first.content_identity() == second.content_identity(),
            message="repeated load_observations() yields equivalent content identity",
            path="/capabilities/property_observations/load_observations",
        )
    )
    # Observable non-aliasing: distinct instances / tuples after two loads.
    results.append(
        check(
            id="property_observations_no_mutable_alias",
            passed=first is not second,
            message="repeated load_observations() does not return the same object alias",
            path="/capabilities/property_observations/load_observations",
        )
    )
    return ConformanceReport(results=tuple(results))


def run_lineage_conformance(case: LineageCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    capability = CapabilityId.LINEAGE
    try:
        instance = _construct(case.registration, capability, case.context)
    except Exception as exc:  # noqa: BLE001
        return ConformanceReport(
            results=(
                check(
                    id="lineage_factory",
                    passed=False,
                    message=f"factory raised {type(exc).__name__}",
                    path="/capabilities/lineage/factory",
                ),
            )
        )
    results.append(
        check(
            id="lineage_factory",
            passed=True,
            message="factory constructed lineage capability",
            path="/capabilities/lineage/factory",
        )
    )
    load_lineage = getattr(instance, "load_lineage", None)
    if not callable(load_lineage):
        results.append(
            check(
                id="lineage_operation",
                passed=False,
                message="capability must implement load_lineage()",
                path="/capabilities/lineage/load_lineage",
            )
        )
        return ConformanceReport(results=tuple(results))

    try:
        first = load_lineage()
        second = load_lineage()
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="lineage_operation",
                passed=False,
                message=f"load_lineage() raised {type(exc).__name__}",
                path="/capabilities/lineage/load_lineage",
            )
        )
        return ConformanceReport(results=tuple(results))

    ok_seq = isinstance(first, Sequence) and not isinstance(first, (str, bytes))
    results.append(
        check(
            id="lineage_operation",
            passed=ok_seq,
            message=(
                "load_lineage() returned a sequence"
                if ok_seq
                else f"load_lineage() returned {type(first).__name__}, expected sequence"
            ),
            path="/capabilities/lineage/load_lineage",
        )
    )
    if not ok_seq or not isinstance(second, Sequence) or isinstance(second, (str, bytes)):
        return ConformanceReport(results=tuple(results))

    bad_types = [
        type(item).__name__ for item in first if not isinstance(item, ColumnLineageAssertion)
    ]
    results.append(
        check(
            id="lineage_item_types",
            passed=not bad_types,
            message=(
                "all lineage items are ColumnLineageAssertion"
                if not bad_types
                else f"non-ColumnLineageAssertion items: {bad_types}"
            ),
            path="/capabilities/lineage/load_lineage",
        )
    )

    # Deterministic by structural tuple equality of public identities when available.
    def _lineage_key(items: Sequence[object]) -> tuple[object, ...]:
        keys: list[object] = []
        for item in items:
            if isinstance(item, ColumnLineageAssertion):
                keys.append(
                    (
                        item.output_column.canonical_bytes(),
                        item.input_column.canonical_bytes(),
                        tuple(record.canonical_sort_key() for record in item.provenance),
                    )
                )
            else:
                keys.append(repr(item))
        return tuple(keys)

    results.append(
        check(
            id="lineage_deterministic",
            passed=_lineage_key(first) == _lineage_key(second),
            message="repeated load_lineage() yields equivalent structural keys",
            path="/capabilities/lineage/load_lineage",
        )
    )
    return ConformanceReport(results=tuple(results))


def run_source_capability_conformance(
    *cases: MetadataDiscoveryCase | GraphCase | ObservationsCase | LineageCase,
) -> ConformanceReport:
    """Run selected source capability suites.

    Partial suite != full provider conformance. Missing declared capabilities
    are not checked here; use run_provider_conformance for completeness.
    """
    reports: list[ConformanceReport] = []
    for case in cases:
        if isinstance(case, MetadataDiscoveryCase):
            reports.append(run_metadata_discovery_conformance(case))
        elif isinstance(case, GraphCase):
            reports.append(run_graph_conformance(case))
        elif isinstance(case, ObservationsCase):
            reports.append(run_observations_conformance(case))
        elif isinstance(case, LineageCase):
            reports.append(run_lineage_conformance(case))
        else:
            raise TypeError(f"unsupported source case: {type(case)!r}")
    if not reports:
        return ConformanceReport(results=())
    return merge_reports(*reports)


__all__ = [
    "run_graph_conformance",
    "run_lineage_conformance",
    "run_metadata_discovery_conformance",
    "run_observations_conformance",
    "run_source_capability_conformance",
]
