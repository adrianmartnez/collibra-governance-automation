"""Vendor-neutral physical GraphNodeIdentity → Collibra/PG local_id projection.

Shared by impact policy matching and reconciliation cross-check. Semantics must
remain identical to the historical ``project_physical_selector_target`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governance.domain.graph import (
    NODE_KIND_COLUMN,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    GraphNodeIdentity,
)
from governance.domain.models import (
    make_column_id,
    make_database_id,
    make_schema_id,
    make_table_id,
)


@dataclass(frozen=True, slots=True)
class PhysicalProjection:
    """Physical projection of a graph identity onto a legacy local_id."""

    object_kind: str
    local_id: str
    node: GraphNodeIdentity

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_id": self.local_id,
            "node": self.node.to_dict(),
            "object_kind": self.object_kind,
        }


def project_physical_local_id(identity: GraphNodeIdentity) -> str | None:
    """Return candidate ``db:/sch:/tbl:/col:`` local_id or ``None`` if not projectable."""
    projected = project_physical_identity(identity)
    return None if projected is None else projected.local_id


def project_physical_identity(identity: GraphNodeIdentity) -> PhysicalProjection | None:
    """Project a graph identity to a physical local_id when the chain is complete.

    Only physical dbt/OpenLineage-style chains are projectable:

    - data_source (no parent) → database
    - dataset → data_source → schema
    - table → dataset → data_source → table
    - column → table → dataset → data_source → column

    Generic ODCS datasets/columns, contracts, transformations, and incomplete
    chains return ``None`` (no coercion). Never parses an existing local_id string.
    """
    if not isinstance(identity, GraphNodeIdentity):
        raise TypeError("identity must be GraphNodeIdentity")

    if identity.kind == NODE_KIND_DATA_SOURCE and identity.parent is None:
        return PhysicalProjection(
            object_kind="database",
            local_id=make_database_id(identity.namespace, identity.logical_id),
            node=identity,
        )

    if identity.kind == NODE_KIND_DATASET:
        parent = identity.parent
        if parent is None or parent.kind != NODE_KIND_DATA_SOURCE or parent.parent is not None:
            return None
        if parent.namespace != identity.namespace:
            return None
        return PhysicalProjection(
            object_kind="schema",
            local_id=make_schema_id(
                identity.namespace,
                parent.logical_id,
                identity.logical_id,
            ),
            node=identity,
        )

    if identity.kind == NODE_KIND_TABLE:
        dataset = identity.parent
        if dataset is None or dataset.kind != NODE_KIND_DATASET:
            return None
        data_source = dataset.parent
        if (
            data_source is None
            or data_source.kind != NODE_KIND_DATA_SOURCE
            or data_source.parent is not None
        ):
            return None
        if identity.namespace != dataset.namespace or dataset.namespace != data_source.namespace:
            return None
        return PhysicalProjection(
            object_kind="table",
            local_id=make_table_id(
                identity.namespace,
                data_source.logical_id,
                dataset.logical_id,
                identity.logical_id,
            ),
            node=identity,
        )

    if identity.kind == NODE_KIND_COLUMN:
        table = identity.parent
        if table is None or table.kind != NODE_KIND_TABLE:
            return None
        dataset = table.parent
        if dataset is None or dataset.kind != NODE_KIND_DATASET:
            return None
        data_source = dataset.parent
        if (
            data_source is None
            or data_source.kind != NODE_KIND_DATA_SOURCE
            or data_source.parent is not None
        ):
            return None
        if (
            identity.namespace != table.namespace
            or table.namespace != dataset.namespace
            or dataset.namespace != data_source.namespace
        ):
            return None
        return PhysicalProjection(
            object_kind="column",
            local_id=make_column_id(
                identity.namespace,
                data_source.logical_id,
                dataset.logical_id,
                table.logical_id,
                identity.logical_id,
            ),
            node=identity,
        )

    return None
