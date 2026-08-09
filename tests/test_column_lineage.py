"""Deterministic column-level lineage semantics (provider-neutral)."""

from __future__ import annotations

import itertools

import pytest

from governance.domain import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    NODE_KIND_COLUMN,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    ColumnLineageAssertion,
    GovernanceGraph,
    GraphEdge,
    GraphNode,
    GraphNodeIdentity,
    ProvenanceRecord,
    materialize_column_lineage_edges,
)

NS = "acme.commerce"


def _table(name: str = "orders") -> GraphNodeIdentity:
    return GraphNodeIdentity(NS, NODE_KIND_TABLE, name)


def _column(name: str, parent: GraphNodeIdentity | None = None) -> GraphNodeIdentity:
    return GraphNodeIdentity(NS, NODE_KIND_COLUMN, name, parent=parent or _table())


def _prov(provider: str, ref: str) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type=provider,
        source_ref=ref,
        source_version=None,
        observation_mode="observed",
    )


def _node(
    identity: GraphNodeIdentity, *, provenance: tuple[ProvenanceRecord, ...] = ()
) -> GraphNode:
    return GraphNode(
        identity=identity,
        name=identity.logical_id,
        description=None,
        attributes={},
        provenance=provenance,
    )


def _graph_with_edges(
    identities: list[GraphNodeIdentity],
    assertions: list[ColumnLineageAssertion],
) -> GovernanceGraph:
    column_ids = [identity for identity in identities if identity.kind == NODE_KIND_COLUMN]
    parents = {col.parent for col in column_ids if col.parent is not None}
    nodes = [_node(parent) for parent in parents] + [_node(col) for col in column_ids]
    edges = list(materialize_column_lineage_edges(assertions))
    for col in column_ids:
        assert col.parent is not None
        edges.append(
            GraphEdge(
                source=col.parent,
                target=col,
                kind=EDGE_KIND_CONTAINS,
                attributes={},
                provenance=(),
            )
        )
    return GovernanceGraph.from_parts(nodes, edges)


def test_assertion_requires_column_endpoints() -> None:
    table = _table()
    col = _column("c")
    with pytest.raises(ValueError, match="column lineage assertion requires column endpoints"):
        ColumnLineageAssertion(output_column=table, input_column=col)
    with pytest.raises(ValueError, match="column lineage assertion requires column endpoints"):
        ColumnLineageAssertion(output_column=col, input_column=table)


def test_materialize_direction_output_depends_on_input() -> None:
    out_c = _column("c", parent=_table("out"))
    in_a = _column("a", parent=_table("in"))
    edges = materialize_column_lineage_edges(
        [
            ColumnLineageAssertion(
                output_column=out_c, input_column=in_a, provenance=(_prov("dbt", "r1"),)
            )
        ]
    )
    assert len(edges) == 1
    assert edges[0].source == out_c
    assert edges[0].target == in_a
    assert edges[0].kind == EDGE_KIND_DEPENDS_ON
    assert edges[0].to_dict()["attributes"] == {}


def test_one_to_one() -> None:
    out_c = _column("c", parent=_table("out"))
    in_a = _column("a", parent=_table("in"))
    edges = materialize_column_lineage_edges(
        [ColumnLineageAssertion(output_column=out_c, input_column=in_a)]
    )
    assert len(edges) == 1


def test_one_output_many_inputs() -> None:
    out = _table("out")
    inn = _table("in")
    out_c = _column("c", parent=out)
    in_a = _column("a", parent=inn)
    in_b = _column("b", parent=inn)
    edges = materialize_column_lineage_edges(
        [
            ColumnLineageAssertion(output_column=out_c, input_column=in_a),
            ColumnLineageAssertion(output_column=out_c, input_column=in_b),
        ]
    )
    assert len(edges) == 2
    pairs = {(e.source, e.target) for e in edges}
    assert pairs == {(out_c, in_a), (out_c, in_b)}


def test_many_outputs_one_input() -> None:
    out = _table("out")
    inn = _table("in")
    out_c = _column("c", parent=out)
    out_d = _column("d", parent=out)
    in_a = _column("a", parent=inn)
    edges = materialize_column_lineage_edges(
        [
            ColumnLineageAssertion(output_column=out_c, input_column=in_a),
            ColumnLineageAssertion(output_column=out_d, input_column=in_a),
        ]
    )
    assert len(edges) == 2
    pairs = {(e.source, e.target) for e in edges}
    assert pairs == {(out_c, in_a), (out_d, in_a)}


def test_duplicate_equivalent_assertions_single_edge() -> None:
    out_c = _column("c", parent=_table("out"))
    in_a = _column("a", parent=_table("in"))
    p1 = _prov("openlineage", "facet-a")
    p2 = _prov("openlineage", "facet-a")
    edges = materialize_column_lineage_edges(
        [
            ColumnLineageAssertion(output_column=out_c, input_column=in_a, provenance=(p1,)),
            ColumnLineageAssertion(output_column=out_c, input_column=in_a, provenance=(p2,)),
        ]
    )
    assert len(edges) == 1
    assert edges[0].provenance == (p1,)


def test_multi_provider_equivalent_union_provenance() -> None:
    out_c = _column("c", parent=_table("out"))
    in_a = _column("a", parent=_table("in"))
    providers = (
        _prov("postgresql", "pg-1"),
        _prov("dbt", "dbt-1"),
        _prov("odcs", "odcs-1"),
        _prov("openlineage", "ol-1"),
    )
    edges = materialize_column_lineage_edges(
        [
            ColumnLineageAssertion(output_column=out_c, input_column=in_a, provenance=(p,))
            for p in providers
        ]
    )
    assert len(edges) == 1
    assert set(edges[0].provenance) == set(providers)
    # Deterministic provenance order from graph merge rules.
    assert list(edges[0].provenance) == sorted(
        providers, key=lambda record: record.canonical_sort_key()
    )


def test_permutation_and_duplicate_invariance() -> None:
    out = _table("out")
    inn = _table("in")
    out_c = _column("c", parent=out)
    in_a = _column("a", parent=inn)
    in_b = _column("b", parent=inn)
    base = [
        ColumnLineageAssertion(
            output_column=out_c,
            input_column=in_a,
            provenance=(_prov("dbt", "a"), _prov("openlineage", "b")),
        ),
        ColumnLineageAssertion(
            output_column=out_c,
            input_column=in_b,
            provenance=(_prov("postgresql", "c"),),
        ),
        ColumnLineageAssertion(
            output_column=out_c,
            input_column=in_a,
            provenance=(_prov("odcs", "d"),),
        ),
    ]
    identities = [out, inn, out_c, in_a, in_b]
    digests: set[str] = set()
    payloads: set[str] = set()
    for perm in itertools.permutations(base):
        duplicated = list(perm) + [base[0], base[1]]
        graph = _graph_with_edges(identities, duplicated)
        digests.add(graph.content_identity().digest)
        payloads.add(repr(graph.to_dict()))
    assert len(digests) == 1
    assert len(payloads) == 1


def test_disagreement_retains_both_edges_no_winner() -> None:
    out_c = _column("c", parent=_table("out"))
    in_a = _column("a", parent=_table("in"))
    in_b = _column("b", parent=_table("in"))
    edges = materialize_column_lineage_edges(
        [
            ColumnLineageAssertion(
                output_column=out_c,
                input_column=in_a,
                provenance=(_prov("dbt", "a"),),
            ),
            ColumnLineageAssertion(
                output_column=out_c,
                input_column=in_b,
                provenance=(_prov("openlineage", "b"),),
            ),
        ]
    )
    assert len(edges) == 2
    by_target = {edge.target: edge for edge in edges}
    assert by_target[in_a].provenance[0].provider_type == "dbt"
    assert by_target[in_b].provenance[0].provider_type == "openlineage"


def test_same_column_name_distinct_parents_are_distinct() -> None:
    parent_a = _table("a")
    parent_b = _table("b")
    col_a = _column("id", parent=parent_a)
    col_b = _column("id", parent=parent_b)
    assert col_a != col_b
    edges = materialize_column_lineage_edges(
        [ColumnLineageAssertion(output_column=col_a, input_column=col_b)]
    )
    assert edges[0].source == col_a
    assert edges[0].target == col_b


def test_non_column_endpoint_error_is_deterministic() -> None:
    dataset = GraphNodeIdentity(NS, NODE_KIND_DATASET, "ds")
    col = _column("c")
    with pytest.raises(
        ValueError, match="column lineage assertion requires column endpoints"
    ) as exc:
        ColumnLineageAssertion(output_column=dataset, input_column=col)
    assert str(exc.value) == "column lineage assertion requires column endpoints"
