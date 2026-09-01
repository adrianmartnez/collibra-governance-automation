"""Unit tests for vendor-neutral physical GraphNodeIdentity projection."""

from __future__ import annotations

import governance
from governance.domain.graph import (
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    NODE_KIND_TRANSFORMATION,
    GraphNodeIdentity,
)
from governance.domain.models import (
    make_column_id,
    make_database_id,
    make_schema_id,
    make_table_id,
)
from governance.domain.physical_projection import (
    project_physical_identity,
    project_physical_local_id,
)
from governance.impact.policy import project_physical_selector_target

NS = "governance-demo"


def _data_source(logical_id: str = "governance_demo") -> GraphNodeIdentity:
    return GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, logical_id)


def _dataset(
    logical_id: str = "commerce",
    *,
    parent: GraphNodeIdentity | None = None,
) -> GraphNodeIdentity:
    return GraphNodeIdentity(
        NS,
        NODE_KIND_DATASET,
        logical_id,
        parent=parent if parent is not None else _data_source(),
    )


def _table(
    logical_id: str = "customers",
    *,
    parent: GraphNodeIdentity | None = None,
) -> GraphNodeIdentity:
    return GraphNodeIdentity(
        NS,
        NODE_KIND_TABLE,
        logical_id,
        parent=parent if parent is not None else _dataset(),
    )


def _column(
    logical_id: str = "customer_id",
    *,
    parent: GraphNodeIdentity | None = None,
) -> GraphNodeIdentity:
    return GraphNodeIdentity(
        NS,
        NODE_KIND_COLUMN,
        logical_id,
        parent=parent if parent is not None else _table(),
    )


def test_package_version_is_1_3_0() -> None:
    assert governance.__version__ == "1.3.0"


def test_project_data_source() -> None:
    identity = _data_source()
    expected = make_database_id(NS, "governance_demo")
    assert project_physical_local_id(identity) == expected
    projected = project_physical_identity(identity)
    assert projected is not None
    assert projected.object_kind == "database"
    assert projected.local_id == expected
    assert projected.node == identity


def test_project_dataset() -> None:
    identity = _dataset()
    expected = make_schema_id(NS, "governance_demo", "commerce")
    assert project_physical_local_id(identity) == expected
    projected = project_physical_identity(identity)
    assert projected is not None
    assert projected.object_kind == "schema"
    assert projected.local_id == expected


def test_project_table() -> None:
    identity = _table()
    expected = make_table_id(NS, "governance_demo", "commerce", "customers")
    assert project_physical_local_id(identity) == expected
    projected = project_physical_identity(identity)
    assert projected is not None
    assert projected.object_kind == "table"
    assert projected.local_id == expected


def test_project_column() -> None:
    identity = _column()
    expected = make_column_id(NS, "governance_demo", "commerce", "customers", "customer_id")
    assert project_physical_local_id(identity) == expected
    projected = project_physical_identity(identity)
    assert projected is not None
    assert projected.object_kind == "column"
    assert projected.local_id == expected


def test_same_namespace_enforced_on_chain() -> None:
    other_ns = GraphNodeIdentity(
        "other.ns",
        NODE_KIND_DATA_SOURCE,
        "governance_demo",
    )
    mismatched = GraphNodeIdentity(NS, NODE_KIND_DATASET, "commerce", parent=other_ns)
    assert project_physical_local_id(mismatched) is None
    assert project_physical_identity(mismatched) is None

    ds = _data_source()
    dataset = _dataset(parent=ds)
    table_other = GraphNodeIdentity("other.ns", NODE_KIND_TABLE, "customers", parent=dataset)
    assert project_physical_local_id(table_other) is None

    table = _table(parent=dataset)
    column_other = GraphNodeIdentity(
        "other.ns",
        NODE_KIND_COLUMN,
        "customer_id",
        parent=table,
    )
    assert project_physical_local_id(column_other) is None


def test_transformation_contract_incomplete_return_none() -> None:
    assert (
        project_physical_local_id(
            GraphNodeIdentity(NS, NODE_KIND_TRANSFORMATION, "model.pkg.orders")
        )
        is None
    )
    assert (
        project_physical_local_id(GraphNodeIdentity(NS, NODE_KIND_CONTRACT, "contract-orders"))
        is None
    )

    # Generic ODCS-style dataset without data_source parent.
    assert project_physical_local_id(GraphNodeIdentity(NS, NODE_KIND_DATASET, "orders")) is None
    # Incomplete table chain (missing data_source ancestor).
    orphan_dataset = GraphNodeIdentity(NS, NODE_KIND_DATASET, "commerce")
    assert (
        project_physical_local_id(
            GraphNodeIdentity(NS, NODE_KIND_TABLE, "customers", parent=orphan_dataset)
        )
        is None
    )
    # data_source with parent is not projectable.
    nested_ds = GraphNodeIdentity(
        NS,
        NODE_KIND_DATA_SOURCE,
        "inner",
        parent=_data_source(),
    )
    assert project_physical_local_id(nested_ds) is None


def test_names_containing_slash_use_make_builders() -> None:
    ds = GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, "db/prod")
    dataset = GraphNodeIdentity(NS, NODE_KIND_DATASET, "sch/a", parent=ds)
    table = GraphNodeIdentity(NS, NODE_KIND_TABLE, "tbl/b", parent=dataset)
    column = GraphNodeIdentity(NS, NODE_KIND_COLUMN, "col/c", parent=table)

    assert project_physical_local_id(ds) == make_database_id(NS, "db/prod")
    assert project_physical_local_id(dataset) == make_schema_id(NS, "db/prod", "sch/a")
    assert project_physical_local_id(table) == make_table_id(NS, "db/prod", "sch/a", "tbl/b")
    assert project_physical_local_id(column) == make_column_id(
        NS, "db/prod", "sch/a", "tbl/b", "col/c"
    )


def test_impact_selector_target_matches_local_id() -> None:
    for identity in (_data_source(), _dataset(), _table(), _column()):
        projected = project_physical_identity(identity)
        selector = project_physical_selector_target(identity)
        assert projected is not None
        assert selector is not None
        assert selector.object_id == projected.local_id
        assert selector.object_kind == projected.object_kind
        assert selector.node == identity
