"""Unit tests for reconciliation safety assessment and target conversion."""

from __future__ import annotations

from governance.domain.authority import (
    AuthorityDeclaration,
    AuthorityRuleKey,
    AuthoritySelector,
    AuthorityTarget,
    NormalizedAuthorityPolicySet,
    NormalizedAuthorityRule,
)
from governance.domain.conflicts import PropertyConflictResult, analyze_property_conflicts
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
    PropertyPath,
)
from governance.reconciliation.safety import (
    REASON_INVALID,
    REASON_NOT_MAPPED,
    REASON_SAFE,
    REASON_UNRESOLVED,
    REASON_UNSUPPORTED,
    assess_reconciliation,
)
from governance.reconciliation.targets import (
    PATH_DATA_TYPE,
    PATH_DESCRIPTION,
    PATH_NAME,
    PATH_OWNERSHIP,
    convert_effective_value,
    is_data_type_applicable,
    is_ownership_applicable,
    path_applicable_to_identity,
)

NS = "acme.commerce"


def _table(logical_id: str = "orders") -> GraphNodeIdentity:
    ds = GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, "analytics")
    dataset = GraphNodeIdentity(NS, NODE_KIND_DATASET, "marts", parent=ds)
    return GraphNodeIdentity(NS, NODE_KIND_TABLE, logical_id, parent=dataset)


def _column(logical_id: str = "order_id") -> GraphNodeIdentity:
    return GraphNodeIdentity(NS, NODE_KIND_COLUMN, logical_id, parent=_table())


def _prov(provider: str, ref: str = "src") -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type=provider,
        source_ref=ref,
        source_version="1.0",
        observation_mode="declared",
    )


def _obs(
    path: PropertyPath,
    value: object,
    *provenances: ProvenanceRecord,
    identity: GraphNodeIdentity | None = None,
) -> PropertyObservation:
    return PropertyObservation(
        object_identity=identity or _table(),
        property_path=path,
        value=value,
        provenance=provenances,
    )


def _result_for(
    *observations: PropertyObservation,
    authority: NormalizedAuthorityPolicySet | None = None,
) -> PropertyConflictResult:
    report = analyze_property_conflicts(
        PropertyObservationSet.from_observations(observations),
        authority if authority is not None else NormalizedAuthorityPolicySet(),
    )
    assert len(report.results) == 1
    return report.results[0]


def test_assessment_matrix_safe_states() -> None:
    single = _result_for(_obs(PATH_DESCRIPTION, "only", _prov("odcs")))
    assert assess_reconciliation(single).reason == REASON_SAFE
    assert assess_reconciliation(single).safe is True
    assert assess_reconciliation(single).applicable is True

    agreement = _result_for(
        _obs(PATH_DESCRIPTION, "shared", _prov("odcs"), _prov("dbt", "m"))
    )
    assert assess_reconciliation(agreement).reason == REASON_SAFE

    authority = NormalizedAuthorityPolicySet(
        rules=(
            NormalizedAuthorityRule(
                key=AuthorityRuleKey(
                    selector=AuthoritySelector(
                        kind=NODE_KIND_TABLE,
                        property_path=PATH_DESCRIPTION,
                    ),
                    authority=AuthorityTarget(provider_type="odcs"),
                ),
                declarations=(AuthorityDeclaration("odcs-wins"),),
            ),
        )
    )
    resolved = _result_for(
        _obs(PATH_DESCRIPTION, "from-odcs", _prov("odcs")),
        _obs(PATH_DESCRIPTION, "from-dbt", _prov("dbt", "m")),
        authority=authority,
    )
    assert assess_reconciliation(resolved).reason == REASON_SAFE
    assert resolved.state == "RESOLVED_BY_AUTHORITY"


def test_assessment_matrix_unresolved_invalid_unsupported_not_mapped() -> None:
    unresolved = _result_for(
        _obs(PATH_DESCRIPTION, "a", _prov("odcs")),
        _obs(PATH_DESCRIPTION, "b", _prov("dbt", "m")),
    )
    assessment = assess_reconciliation(unresolved)
    assert assessment.applicable is True
    assert assessment.safe is False
    assert assessment.reason == REASON_UNRESOLVED

    selector = AuthoritySelector(kind=NODE_KIND_TABLE, property_path=PATH_DESCRIPTION)
    invalid_authority = NormalizedAuthorityPolicySet(
        rules=(
            NormalizedAuthorityRule(
                key=AuthorityRuleKey(
                    selector=selector,
                    authority=AuthorityTarget(provider_type="odcs"),
                ),
                declarations=(AuthorityDeclaration("a"),),
            ),
            NormalizedAuthorityRule(
                key=AuthorityRuleKey(
                    selector=selector,
                    authority=AuthorityTarget(provider_type="dbt"),
                ),
                declarations=(AuthorityDeclaration("b"),),
            ),
        )
    )
    invalid = _result_for(
        _obs(PATH_DESCRIPTION, "a", _prov("odcs")),
        _obs(PATH_DESCRIPTION, "b", _prov("dbt", "m")),
        authority=invalid_authority,
    )
    assert invalid.state == "INVALID_OR_AMBIGUOUS_AUTHORITY"
    assert assess_reconciliation(invalid).reason == REASON_INVALID

    # Safe state but non-representable effective value (non-string name).
    bad_name = _result_for(_obs(PATH_NAME, 123, _prov("odcs")))
    assert assess_reconciliation(bad_name).reason == REASON_UNSUPPORTED

    unmapped_path = PropertyPath(("attributes", "storage_layer"))
    unmapped = _result_for(_obs(unmapped_path, "iceberg", _prov("openlineage")))
    not_mapped = assess_reconciliation(unmapped)
    assert not_mapped.applicable is False
    assert not_mapped.safe is False
    assert not_mapped.reason == REASON_NOT_MAPPED


def test_ownership_conversion_single_owner_ok() -> None:
    identity = _table()
    value = [{"name": "data-team", "type": "team"}]
    converted = convert_effective_value(
        path=PATH_OWNERSHIP,
        identity=identity,
        value=value,
        has_effective_value=True,
    )
    assert converted is not None
    assert converted.field == "owner"
    assert converted.has_value is True
    assert converted.value == "data-team"

    result = _result_for(
        _obs(PATH_OWNERSHIP, value, _prov("openlineage"), identity=identity),
    )
    assert assess_reconciliation(result).reason == REASON_SAFE


def test_ownership_conversion_multi_owner_unsupported() -> None:
    identity = _table()
    value = [
        {"name": "data-team", "type": "team"},
        {"name": "platform", "type": "team"},
    ]
    assert (
        convert_effective_value(
            path=PATH_OWNERSHIP,
            identity=identity,
            value=value,
            has_effective_value=True,
        )
        is None
    )
    result = _result_for(
        _obs(PATH_OWNERSHIP, value, _prov("openlineage"), identity=identity),
    )
    assessment = assess_reconciliation(result)
    assert assessment.applicable is True
    assert assessment.safe is False
    assert assessment.reason == REASON_UNSUPPORTED


def test_column_ownership_not_mapped() -> None:
    identity = _column()
    assert is_ownership_applicable(identity.kind) is False
    assert path_applicable_to_identity(PATH_OWNERSHIP, identity) is False
    result = _result_for(
        _obs(
            PATH_OWNERSHIP,
            [{"name": "x", "type": "user"}],
            _prov("odcs"),
            identity=identity,
        ),
    )
    assessment = assess_reconciliation(result)
    assert assessment.reason == REASON_NOT_MAPPED
    assert assessment.applicable is False
    assert assessment.safe is False


def test_data_type_only_for_column() -> None:
    table = _table()
    column = _column()
    assert is_data_type_applicable(NODE_KIND_COLUMN) is True
    assert is_data_type_applicable(NODE_KIND_TABLE) is False
    assert path_applicable_to_identity(PATH_DATA_TYPE, column) is True
    assert path_applicable_to_identity(PATH_DATA_TYPE, table) is False

    for kind, identity in (
        (NODE_KIND_DATA_SOURCE, GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, "db")),
        (NODE_KIND_DATASET, GraphNodeIdentity(NS, NODE_KIND_DATASET, "sch")),
        (NODE_KIND_TABLE, table),
    ):
        assert is_ownership_applicable(kind) is True
        result = _result_for(
            _obs(PATH_DATA_TYPE, "varchar", _prov("dbt"), identity=identity),
        )
        assert assess_reconciliation(result).reason == REASON_NOT_MAPPED

    col_result = _result_for(
        _obs(PATH_DATA_TYPE, "varchar", _prov("dbt"), identity=column),
    )
    assert assess_reconciliation(col_result).reason == REASON_SAFE
