"""Deterministic column-level lineage assertions on GovernanceGraph.

Semantic data-flow ``input_column -> output_column`` materializes as
``output_column --depends_on--> input_column`` (same direction as table lineage).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from governance.domain.graph import (
    EDGE_KIND_DEPENDS_ON,
    NODE_KIND_COLUMN,
    GraphEdge,
    GraphNodeIdentity,
    ProvenanceRecord,
    _merge_provenance,
)


@dataclass(frozen=True, slots=True)
class ColumnLineageAssertion:
    """One observed/declared dependency from an output column to an input column."""

    output_column: GraphNodeIdentity
    input_column: GraphNodeIdentity
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.output_column, GraphNodeIdentity):
            raise TypeError("output_column must be GraphNodeIdentity")
        if not isinstance(self.input_column, GraphNodeIdentity):
            raise TypeError("input_column must be GraphNodeIdentity")
        if self.output_column.kind != NODE_KIND_COLUMN:
            raise ValueError("column lineage assertion requires column endpoints")
        if self.input_column.kind != NODE_KIND_COLUMN:
            raise ValueError("column lineage assertion requires column endpoints")
        provenance = (
            tuple(self.provenance) if not isinstance(self.provenance, tuple) else self.provenance
        )
        for record in provenance:
            if not isinstance(record, ProvenanceRecord):
                raise TypeError("provenance entries must be ProvenanceRecord")
        object.__setattr__(self, "provenance", _merge_provenance(provenance))


def materialize_column_lineage_edges(
    assertions: Sequence[ColumnLineageAssertion],
) -> tuple[GraphEdge, ...]:
    """Deduplicate equivalent assertions and emit ``depends_on`` edges.

    Logical key is ``(output_column, input_column)``. Equivalent observations
    union provenance deterministically. Distinct input/output pairs remain as
    separate edges (provider disagreement stays visible; no authority winner).
    """
    merged: dict[tuple[GraphNodeIdentity, GraphNodeIdentity], list[ProvenanceRecord]] = {}
    for assertion in assertions:
        if not isinstance(assertion, ColumnLineageAssertion):
            raise TypeError("assertions must be ColumnLineageAssertion")
        key = (assertion.output_column, assertion.input_column)
        bucket = merged.setdefault(key, [])
        bucket.extend(assertion.provenance)

    edges = [
        GraphEdge(
            source=output_column,
            target=input_column,
            kind=EDGE_KIND_DEPENDS_ON,
            attributes={},
            provenance=tuple(records),
        )
        for (output_column, input_column), records in merged.items()
    ]
    edges.sort(key=lambda edge: edge.logical_sort_key())
    return tuple(edges)
