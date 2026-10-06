"""Self-tests for the public Provider SDK conformance kit."""

from __future__ import annotations

import pytest

from governance.conformance import (
    ConformanceFailure,
    GraphCase,
    MetadataDiscoveryCase,
    MutationCase,
    ObservationsCase,
    RegistrationCase,
    SecretSafetyCase,
    assert_conformance,
    empty_runtime_context,
    run_provider_conformance,
    run_registration_conformance,
    run_source_capability_conformance,
)
from governance.domain.graph import (
    NODE_KIND_DATASET,
    GovernanceGraph,
    GraphNode,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.models import Column, Database, DataSource, GovernanceModel, Schema, Table
from governance.domain.observations import (
    PropertyObservation,
    PropertyObservationSet,
    PropertyPath,
)
from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRuntimeContext,
)


def _prov() -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type="example",
        source_ref="fixture",
        observation_mode="declared",
    )


def _dataset_node(logical_id: str = "orders") -> GraphNode:
    identity = GraphNodeIdentity(
        namespace="demo",
        kind=NODE_KIND_DATASET,
        logical_id=logical_id,
    )
    return GraphNode(identity=identity, name=logical_id, provenance=(_prov(),))


def _good_graph() -> GovernanceGraph:
    return GovernanceGraph.from_parts([_dataset_node()], ())


def _good_observations() -> PropertyObservationSet:
    node = _dataset_node()
    return PropertyObservationSet.from_observations(
        [
            PropertyObservation(
                object_identity=node.identity,
                property_path=PropertyPath.parse("/name"),
                value="orders",
                provenance=(_prov(),),
            )
        ]
    )


def _register_graph_obs(*, nondeterministic: bool = False) -> ProviderRegistration:
    counter = {"n": 0}

    def graph_factory(context: ProviderRuntimeContext) -> object:
        _ = context

        class Cap:
            def load_graph(self) -> GovernanceGraph:
                if nondeterministic:
                    counter["n"] += 1
                    return GovernanceGraph.from_parts(
                        [_dataset_node(f"orders-{counter['n']}")],
                        (),
                    )
                return _good_graph()

        return Cap()

    def observations_factory(context: ProviderRuntimeContext) -> object:
        _ = context

        class Cap:
            def load_observations(self) -> PropertyObservationSet:
                return _good_observations()

        return Cap()

    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="acme.demo",
            display_name="Acme Demo",
            provider_version="0.1.0",
            sdk_compatibility=">=1,<2",
            capabilities=(
                CapabilityId.GOVERNANCE_GRAPH,
                CapabilityId.PROPERTY_OBSERVATIONS,
            ),
        ),
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.GOVERNANCE_GRAPH,
                factory=graph_factory,
            ),
            CapabilityBinding(
                capability_id=CapabilityId.PROPERTY_OBSERVATIONS,
                factory=observations_factory,
            ),
        ),
    )


def test_full_conformance_passes_with_all_scenarios() -> None:
    registration = _register_graph_obs()
    context = empty_runtime_context({"path": "catalog.json", "namespace": "demo"})
    report = run_provider_conformance(
        register=RegistrationCase(register=lambda: registration),
        scenarios=(
            GraphCase(registration=registration, context=context),
            ObservationsCase(registration=registration, context=context),
        ),
    )
    assert_conformance(report)
    assert report.passed


def test_full_conformance_fails_when_scenario_missing() -> None:
    registration = _register_graph_obs()
    context = empty_runtime_context()
    report = run_provider_conformance(
        register=RegistrationCase(register=lambda: registration),
        scenarios=(GraphCase(registration=registration, context=context),),
    )
    assert not report.passed
    missing = [item for item in report.failures if item.id == "missing_conformance_scenario"]
    assert len(missing) == 1
    assert "property_observations" in missing[0].message
    with pytest.raises(ConformanceFailure):
        assert_conformance(report)


def test_partial_source_suite_allows_subset() -> None:
    registration = _register_graph_obs()
    context = empty_runtime_context()
    report = run_source_capability_conformance(
        GraphCase(registration=registration, context=context),
    )
    assert report.passed


def test_registration_deterministic() -> None:
    report = run_registration_conformance(
        RegistrationCase(register=lambda: _register_graph_obs()),
    )
    assert report.passed


def test_non_deterministic_graph_fails() -> None:
    registration = _register_graph_obs(nondeterministic=True)
    context = empty_runtime_context()
    report = run_provider_conformance(
        register=RegistrationCase(register=lambda: registration),
        scenarios=(
            GraphCase(registration=registration, context=context),
            ObservationsCase(registration=registration, context=context),
        ),
    )
    assert not report.passed
    assert any(item.id == "governance_graph_deterministic" for item in report.failures)


def test_false_capability_implementation_fails() -> None:
    def graph_factory(context: ProviderRuntimeContext) -> object:
        _ = context

        class Broken:
            def load_graph(self) -> object:
                return {"not": "a graph"}

        return Broken()

    def observations_factory(context: ProviderRuntimeContext) -> object:
        _ = context

        class Cap:
            def load_observations(self) -> PropertyObservationSet:
                return _good_observations()

        return Cap()

    registration = ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="acme.broken",
            display_name="Broken",
            provider_version="0.1.0",
            sdk_compatibility=">=1,<2",
            capabilities=(
                CapabilityId.GOVERNANCE_GRAPH,
                CapabilityId.PROPERTY_OBSERVATIONS,
            ),
        ),
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.GOVERNANCE_GRAPH,
                factory=graph_factory,
            ),
            CapabilityBinding(
                capability_id=CapabilityId.PROPERTY_OBSERVATIONS,
                factory=observations_factory,
            ),
        ),
    )
    context = empty_runtime_context()
    report = run_provider_conformance(
        register=RegistrationCase(register=lambda: registration),
        scenarios=(
            GraphCase(registration=registration, context=context),
            ObservationsCase(registration=registration, context=context),
        ),
    )
    assert not report.passed
    assert any(item.id == "governance_graph_operation" for item in report.failures)


def test_secret_safety_observes_repr() -> None:
    secret = "super-secret-token-value"
    registration = _register_graph_obs()
    context = ProviderRuntimeContext(config={"token": secret}, config_root=None)
    report = run_provider_conformance(
        register=RegistrationCase(register=lambda: registration),
        scenarios=(
            GraphCase(registration=registration, context=context),
            ObservationsCase(registration=registration, context=context),
        ),
        secret_safety=SecretSafetyCase(
            registration=registration,
            context=context,
            forbidden_substrings=(secret,),
        ),
    )
    assert report.passed


def test_mutation_requires_authorized_flag() -> None:
    mutations = {"n": 0}

    def mutation_factory(context: ProviderRuntimeContext) -> object:
        _ = context

        class Cap:
            def execute_authorized(self, request: object) -> object:
                mutations["n"] += 1
                return {"ok": True, "request": request}

        return Cap()

    registration = ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="acme.target",
            display_name="Target",
            provider_version="0.1.0",
            sdk_compatibility=">=1,<2",
            capabilities=(CapabilityId.AUTHORIZED_MUTATION,),
        ),
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.AUTHORIZED_MUTATION,
                factory=mutation_factory,
            ),
        ),
    )
    context = empty_runtime_context()
    unauthorized = run_provider_conformance(
        register=RegistrationCase(register=lambda: registration),
        scenarios=(
            MutationCase(
                registration=registration,
                context=context,
                request={"op": "x"},
                authorized=False,
            ),
        ),
    )
    assert not unauthorized.passed
    assert mutations["n"] == 0

    authorized = run_provider_conformance(
        register=RegistrationCase(register=lambda: registration),
        scenarios=(
            MutationCase(
                registration=registration,
                context=context,
                request={"op": "x"},
                authorized=True,
            ),
        ),
    )
    assert authorized.passed
    assert mutations["n"] == 1


def test_metadata_discovery_partial_suite() -> None:
    def factory(context: ProviderRuntimeContext) -> object:
        _ = context

        class Cap:
            def discover(self) -> GovernanceModel:
                return GovernanceModel(
                    data_sources=(
                        DataSource(
                            id="ds:demo",
                            name="demo",
                            system_type="fixture",
                            databases=(
                                Database(
                                    id="db:demo/main",
                                    name="main",
                                    datasource_id="ds:demo",
                                    schemas=(
                                        Schema(
                                            id="sch:demo/main/public",
                                            name="public",
                                            database_id="db:demo/main",
                                            tables=(
                                                Table(
                                                    id="tbl:demo/main/public/orders",
                                                    name="orders",
                                                    schema_id="sch:demo/main/public",
                                                    columns=(
                                                        Column(
                                                            id="col:demo/main/public/orders/id",
                                                            name="id",
                                                            data_type="int",
                                                            ordinal_position=1,
                                                        ),
                                                    ),
                                                ),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    )
                )

        return Cap()

    registration = ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="acme.meta",
            display_name="Meta",
            provider_version="0.1.0",
            sdk_compatibility=">=1,<2",
            capabilities=(CapabilityId.METADATA_DISCOVERY,),
        ),
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.METADATA_DISCOVERY,
                factory=factory,
            ),
        ),
    )
    report = run_source_capability_conformance(
        MetadataDiscoveryCase(
            registration=registration,
            context=empty_runtime_context(),
        )
    )
    assert report.passed


def test_incompatible_sdk_fails_registration_suite() -> None:
    def register() -> ProviderRegistration:
        return ProviderRegistration(
            descriptor=ProviderDescriptor(
                provider_id="acme.future",
                display_name="Future",
                provider_version="0.1.0",
                sdk_compatibility=">=2,<3",
                capabilities=(CapabilityId.LINEAGE,),
            ),
            bindings=(
                CapabilityBinding(
                    capability_id=CapabilityId.LINEAGE,
                    factory=lambda context: type(
                        "L",
                        (),
                        {"load_lineage": lambda self: ()},
                    )(),
                ),
            ),
        )

    report = run_registration_conformance(RegistrationCase(register=register))
    assert not report.passed
    assert any(
        item.id in {"sdk_compatibility_membership", "registration_registry_accepts"}
        for item in report.failures
    )
