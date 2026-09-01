"""Unit tests for reconciliation assumptions boundary and safety validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.domain.conflicts import PropertyConflictReport
from governance.domain.graph import (
    NODE_KIND_COLUMN,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.observations import (
    PropertyObservation,
    PropertyObservationSet,
)
from governance.integrations.collibra.mapping import load_mapping_config_file
from governance.integrations.collibra.models import (
    CollibraAssetSpec,
    CollibraDesiredState,
    CollibraRemoteState,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
)
from governance.reconciliation.assumptions import (
    assumptions_content_identity,
    build_reconciliation_assumptions,
    recompute_assumptions_on_saved_boundary,
    validate_assumptions_safety,
)
from governance.reconciliation.errors import (
    CODE_UNRESOLVED_PROPERTY_CONFLICT,
    ReconciliationError,
)
from governance.reconciliation.physical_index import PhysicalReconciliationIndex
from governance.reconciliation.targets import PATH_DESCRIPTION

MAPPING = Path(__file__).resolve().parent / "fixtures" / "governance_yaml" / "mapping.json"
NS = "governance-demo"


def _mapping():
    return load_mapping_config_file(MAPPING)


def _table_identity() -> GraphNodeIdentity:
    ds = GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, "governance_demo")
    dataset = GraphNodeIdentity(NS, NODE_KIND_DATASET, "commerce", parent=ds)
    return GraphNodeIdentity(NS, NODE_KIND_TABLE, "customers", parent=dataset)


def _column_identity() -> GraphNodeIdentity:
    return GraphNodeIdentity(
        NS,
        NODE_KIND_COLUMN,
        "customer_id",
        parent=_table_identity(),
    )


def _asset(local_id: str, name: str) -> CollibraAssetSpec:
    return CollibraAssetSpec(
        local_id=local_id,
        name=name,
        asset_type_ref="mock:asset-type:table",
        domain_ref="mock:domain:governance",
        display_name=name,
    )


def test_create_table_no_sources_boundary_null_decisions() -> None:
    identity = _table_identity()
    local_id = "tbl:governance-demo/governance_demo/commerce/customers"
    asset = _asset(local_id, "customers")
    desired = CollibraDesiredState(assets=(asset,))
    index = PhysicalReconciliationIndex(by_local_id={local_id: identity}, namespace=NS)
    sync_plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id=local_id,
                desired_asset=asset,
                reason="missing remotely",
            ),
        )
    )
    assumptions = build_reconciliation_assumptions(
        baseline_desired=desired,
        reconciled_desired=desired,
        remote_state=CollibraRemoteState(),
        sync_plan=sync_plan,
        conflict_report=PropertyConflictReport(),
        mapping_config=_mapping(),
        physical_index=index,
    )
    assert len(assumptions["actions"]) == 1
    props = assumptions["actions"][0]["properties"]
    pointers = {item["property"] for item in props}
    assert pointers == {"/name", "/description", "/attributes/ownership"}
    assert all(item["decision"] is None for item in props)
    assert all(item["roles"] == ["mutation"] for item in props)


def test_create_column_boundary_no_ownership() -> None:
    identity = _column_identity()
    local_id = "col:governance-demo/governance_demo/commerce/customers/customer_id"
    asset = CollibraAssetSpec(
        local_id=local_id,
        name="customer_id",
        asset_type_ref="mock:asset-type:column",
        domain_ref="mock:domain:governance",
        display_name="customer_id",
    )
    desired = CollibraDesiredState(assets=(asset,))
    index = PhysicalReconciliationIndex(by_local_id={local_id: identity}, namespace=NS)
    sync_plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id=local_id,
                desired_asset=asset,
                reason="missing remotely",
            ),
        )
    )
    assumptions = build_reconciliation_assumptions(
        baseline_desired=desired,
        reconciled_desired=desired,
        remote_state=CollibraRemoteState(),
        sync_plan=sync_plan,
        conflict_report=PropertyConflictReport(),
        mapping_config=_mapping(),
        physical_index=index,
    )
    props = assumptions["actions"][0]["properties"]
    pointers = {item["property"] for item in props}
    assert pointers == {"/name", "/description", "/attributes/data_type"}
    assert "/attributes/ownership" not in pointers
    assert all(item["decision"] is None for item in props)


def test_recompute_null_to_present_changes_identity() -> None:
    identity = _table_identity()
    local_id = "tbl:governance-demo/governance_demo/commerce/customers"
    saved = {
        "actions": [
            {
                "action_type": "create",
                "local_id": local_id,
                "object_kind": "asset",
                "properties": [
                    {
                        "decision": None,
                        "object": identity.to_dict(),
                        "property": "/description",
                        "roles": ["mutation"],
                    }
                ],
            }
        ],
        "assumptions_schema": "governance-reconciliation-assumptions",
        "assumptions_version": "1",
    }
    before = assumptions_content_identity(saved)

    observation = PropertyObservation(
        object_identity=identity,
        property_path=PATH_DESCRIPTION,
        value="customers table",
        provenance=(
            ProvenanceRecord(
                provider_type="dbt",
                source_ref="model.pkg.customers",
                source_version="1.0",
                observation_mode="declared",
            ),
        ),
    )
    from governance.domain.authority import NormalizedAuthorityPolicySet
    from governance.domain.conflicts import analyze_property_conflicts

    report = analyze_property_conflicts(
        PropertyObservationSet.from_observations((observation,)),
        NormalizedAuthorityPolicySet(),
    )
    recomputed = recompute_assumptions_on_saved_boundary(
        saved_assumptions=saved,
        conflict_report=report,
    )
    after = assumptions_content_identity(recomputed)
    assert recomputed["actions"][0]["properties"][0]["decision"] is not None
    assert recomputed["actions"][0]["properties"][0]["decision"]["state"] == (
        "SINGLE_OBSERVATION"
    )
    assert before != after


def test_validate_assumptions_safety_blocks_unresolved_material() -> None:
    identity = _table_identity()
    from governance.domain.authority import NormalizedAuthorityPolicySet
    from governance.domain.conflicts import analyze_property_conflicts

    observations = PropertyObservationSet.from_observations(
        (
            PropertyObservation(
                object_identity=identity,
                property_path=PATH_DESCRIPTION,
                value="a",
                provenance=(
                    ProvenanceRecord(
                        provider_type="odcs",
                        source_ref="c1",
                        source_version="1.0",
                        observation_mode="declared",
                    ),
                ),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=PATH_DESCRIPTION,
                value="b",
                provenance=(
                    ProvenanceRecord(
                        provider_type="dbt",
                        source_ref="m",
                        source_version="1.0",
                        observation_mode="declared",
                    ),
                ),
            ),
        )
    )
    report = analyze_property_conflicts(observations, NormalizedAuthorityPolicySet())
    result = report.results[0]
    assert result.state == "UNRESOLVED_CONFLICT"

    assumptions = {
        "actions": [
            {
                "action_type": "create",
                "local_id": "tbl:x",
                "object_kind": "asset",
                "properties": [
                    {
                        "decision": {
                            "reason": result.reason,
                            "state": result.state,
                            "value_groups": [
                                group.to_value_group_dict() for group in result.value_groups
                            ],
                        },
                        "object": identity.to_dict(),
                        "property": "/description",
                        "roles": ["mutation"],
                    }
                ],
            }
        ],
        "assumptions_schema": "governance-reconciliation-assumptions",
        "assumptions_version": "1",
    }
    with pytest.raises(ReconciliationError) as exc:
        validate_assumptions_safety(assumptions, report)
    assert exc.value.errors[0].code == CODE_UNRESOLVED_PROPERTY_CONFLICT


def test_unrelated_unresolved_conflict_does_not_block_safety() -> None:
    """Unresolved conflict outside the saved boundary must not fail closed."""
    from governance.domain.authority import NormalizedAuthorityPolicySet
    from governance.domain.conflicts import analyze_property_conflicts

    identity = _table_identity()
    other = GraphNodeIdentity(
        NS,
        NODE_KIND_TABLE,
        "ghost",
        parent=identity.parent,
    )
    observations = PropertyObservationSet.from_observations(
        (
            PropertyObservation(
                object_identity=other,
                property_path=PATH_DESCRIPTION,
                value="a",
                provenance=(
                    ProvenanceRecord(
                        provider_type="odcs",
                        source_ref="c1",
                        source_version="1.0",
                        observation_mode="declared",
                    ),
                ),
            ),
            PropertyObservation(
                object_identity=other,
                property_path=PATH_DESCRIPTION,
                value="b",
                provenance=(
                    ProvenanceRecord(
                        provider_type="dbt",
                        source_ref="m",
                        source_version="1.0",
                        observation_mode="declared",
                    ),
                ),
            ),
        )
    )
    report = analyze_property_conflicts(observations, NormalizedAuthorityPolicySet())
    assert any(item.state == "UNRESOLVED_CONFLICT" for item in report.results)

    # Boundary only covers customers /name — ghost description is unrelated.
    assumptions = {
        "actions": [
            {
                "action_type": "create",
                "local_id": "tbl:governance-demo/governance_demo/commerce/customers",
                "object_kind": "asset",
                "properties": [
                    {
                        "decision": None,
                        "object": identity.to_dict(),
                        "property": "/name",
                        "roles": ["mutation"],
                    }
                ],
            }
        ],
        "assumptions_schema": "governance-reconciliation-assumptions",
        "assumptions_version": "1",
    }
    validate_assumptions_safety(assumptions, report)
