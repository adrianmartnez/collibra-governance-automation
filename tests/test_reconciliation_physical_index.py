"""Unit tests for PhysicalReconciliationIndex and cross-check."""

from __future__ import annotations

import pytest

from governance.domain import (
    Column,
    Database,
    DataSource,
    GovernanceModel,
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_schema_id,
    make_table_id,
)
from governance.domain.graph import (
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    NODE_KIND_TRANSFORMATION,
    GraphNodeIdentity,
)
from governance.reconciliation.errors import (
    CODE_OBJECT_IDENTITY_CONFLICT,
    ReconciliationError,
    objects_diagnostic_path,
)
from governance.reconciliation.physical_index import (
    PhysicalReconciliationIndex,
    build_physical_reconciliation_index,
    cross_check_known_objects,
)

NS = "governance-demo"
DB = "governance_demo"
SCH = "commerce"
TBL = "customers"
COL = "customer_id"


def _model() -> GovernanceModel:
    table_id = make_table_id(NS, DB, SCH, TBL)
    col_id = make_column_id(NS, DB, SCH, TBL, COL)
    return GovernanceModel(
        data_sources=(
            DataSource(
                id=make_datasource_id(NS),
                name=NS,
                system_type="postgresql",
                databases=(
                    Database(
                        id=make_database_id(NS, DB),
                        name=DB,
                        datasource_id=make_datasource_id(NS),
                        schemas=(
                            Schema(
                                id=make_schema_id(NS, DB, SCH),
                                name=SCH,
                                database_id=make_database_id(NS, DB),
                                tables=(
                                    Table(
                                        id=table_id,
                                        name=TBL,
                                        schema_id=make_schema_id(NS, DB, SCH),
                                        columns=(
                                            Column(
                                                id=col_id,
                                                name=COL,
                                                data_type="uuid",
                                                ordinal_position=1,
                                                nullable=False,
                                            ),
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


def _physical_chain(
    *,
    db: str = DB,
    schema: str = SCH,
    table: str = TBL,
    column: str | None = None,
) -> GraphNodeIdentity:
    ds = GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, db)
    dataset = GraphNodeIdentity(NS, NODE_KIND_DATASET, schema, parent=ds)
    table_id = GraphNodeIdentity(NS, NODE_KIND_TABLE, table, parent=dataset)
    if column is None:
        return table_id
    return GraphNodeIdentity(NS, NODE_KIND_COLUMN, column, parent=table_id)


def test_build_index_from_governance_model() -> None:
    index = build_physical_reconciliation_index(_model(), namespace=NS)
    assert isinstance(index, PhysicalReconciliationIndex)
    assert index.namespace == NS

    db_id = make_database_id(NS, DB)
    sch_id = make_schema_id(NS, DB, SCH)
    tbl_id = make_table_id(NS, DB, SCH, TBL)
    col_id = make_column_id(NS, DB, SCH, TBL, COL)

    assert index.get(db_id) == GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, DB)
    assert index.get(sch_id) == GraphNodeIdentity(
        NS,
        NODE_KIND_DATASET,
        SCH,
        parent=GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, DB),
    )
    assert index.get(tbl_id) == _physical_chain()
    assert index.get(col_id) == _physical_chain(column=COL)
    assert set(index.by_local_id) == {db_id, sch_id, tbl_id, col_id}


def test_cross_check_ok_same_identity() -> None:
    index = build_physical_reconciliation_index(_model(), namespace=NS)
    cross_check_known_objects(
        known_objects=(_physical_chain(), _physical_chain(column=COL)),
        physical_index=index,
    )


def test_cross_check_raises_on_mismatch_with_index() -> None:
    local_id = make_table_id(NS, DB, SCH, TBL)
    ds = GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, DB)
    dataset = GraphNodeIdentity(NS, NODE_KIND_DATASET, SCH, parent=ds)
    expected = GraphNodeIdentity(NS, NODE_KIND_TABLE, TBL, parent=dataset)
    alien = GraphNodeIdentity(NS, NODE_KIND_TABLE, "other", parent=dataset)
    manual = PhysicalReconciliationIndex(by_local_id={local_id: alien}, namespace=NS)

    with pytest.raises(ReconciliationError) as exc:
        cross_check_known_objects(known_objects=(expected,), physical_index=manual)
    assert len(exc.value.errors) == 1
    assert exc.value.errors[0].code == CODE_OBJECT_IDENTITY_CONFLICT
    assert exc.value.errors[0].path == objects_diagnostic_path(local_id)


def test_external_only_projectable_absent_from_pg_no_error() -> None:
    index = build_physical_reconciliation_index(_model(), namespace=NS)
    cross_check_known_objects(
        known_objects=(_physical_chain(table="ghost_table"),),
        physical_index=index,
    )


def test_non_projectable_ignored() -> None:
    index = build_physical_reconciliation_index(_model(), namespace=NS)
    cross_check_known_objects(
        known_objects=(
            GraphNodeIdentity(NS, NODE_KIND_TRANSFORMATION, "model.pkg.x"),
            GraphNodeIdentity(NS, NODE_KIND_CONTRACT, "contract-x"),
            GraphNodeIdentity(NS, NODE_KIND_DATASET, "generic-odcs"),
        ),
        physical_index=index,
    )


def test_same_candidate_different_identities_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = PhysicalReconciliationIndex(by_local_id={}, namespace=NS)
    ds = GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, DB)
    first = GraphNodeIdentity(NS, NODE_KIND_DATASET, "a", parent=ds)
    second = GraphNodeIdentity(NS, NODE_KIND_DATASET, "b", parent=ds)
    assert first != second

    def fake_project(identity: GraphNodeIdentity) -> str | None:
        if identity in {first, second}:
            return "sch:collision"
        return None

    monkeypatch.setattr(
        "governance.reconciliation.physical_index.project_physical_local_id",
        fake_project,
    )
    with pytest.raises(ReconciliationError) as exc:
        cross_check_known_objects(
            known_objects=(first, second),
            physical_index=index,
        )
    assert exc.value.errors[0].code == CODE_OBJECT_IDENTITY_CONFLICT
    assert exc.value.errors[0].path == objects_diagnostic_path("sch:collision")


def test_reversed_order_same_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    index = PhysicalReconciliationIndex(by_local_id={}, namespace=NS)
    ds = GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, DB)
    first = GraphNodeIdentity(NS, NODE_KIND_DATASET, "a", parent=ds)
    second = GraphNodeIdentity(NS, NODE_KIND_DATASET, "b", parent=ds)

    def fake_project(identity: GraphNodeIdentity) -> str | None:
        if identity in {first, second}:
            return "sch:collision"
        return None

    monkeypatch.setattr(
        "governance.reconciliation.physical_index.project_physical_local_id",
        fake_project,
    )
    with pytest.raises(ReconciliationError) as forward:
        cross_check_known_objects(
            known_objects=(first, second),
            physical_index=index,
        )
    with pytest.raises(ReconciliationError) as reverse:
        cross_check_known_objects(
            known_objects=(second, first),
            physical_index=index,
        )
    assert forward.value.to_diagnostics() == reverse.value.to_diagnostics()
