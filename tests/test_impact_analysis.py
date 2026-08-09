"""Deterministic downstream blast-radius / impact analysis."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from governance.domain import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    EDGE_KIND_GOVERNS,
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    NODE_KIND_TRANSFORMATION,
    GovernanceGraph,
    GovernanceImpactResult,
    GraphEdge,
    GraphNode,
    GraphNodeIdentity,
    ImpactPath,
    ImpactStep,
    ProvenanceRecord,
    analyze_downstream_impact,
)

NS = "acme.commerce"


def _id(kind: str, logical_id: str, parent: GraphNodeIdentity | None = None) -> GraphNodeIdentity:
    return GraphNodeIdentity(NS, kind, logical_id, parent=parent)


def _table(name: str) -> GraphNodeIdentity:
    return _id(NODE_KIND_TABLE, name)


def _column(name: str, parent: GraphNodeIdentity) -> GraphNodeIdentity:
    return _id(NODE_KIND_COLUMN, name, parent=parent)


def _prov(provider: str, ref: str) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type=provider,
        source_ref=ref,
        source_version=None,
        observation_mode="observed",
    )


def _node(
    identity: GraphNodeIdentity,
    *,
    provenance: tuple[ProvenanceRecord, ...] = (),
    attributes: dict[str, Any] | None = None,
) -> GraphNode:
    return GraphNode(
        identity=identity,
        name=identity.logical_id,
        description=None,
        attributes=attributes if attributes is not None else {},
        provenance=provenance,
    )


def _depends(derived: GraphNodeIdentity, dependency: GraphNodeIdentity) -> GraphEdge:
    return GraphEdge(source=derived, target=dependency, kind=EDGE_KIND_DEPENDS_ON)


def _contains(parent: GraphNodeIdentity, child: GraphNodeIdentity) -> GraphEdge:
    return GraphEdge(source=parent, target=child, kind=EDGE_KIND_CONTAINS)


def _governs(contract: GraphNodeIdentity, asset: GraphNodeIdentity) -> GraphEdge:
    return GraphEdge(source=contract, target=asset, kind=EDGE_KIND_GOVERNS)


def _graph(
    identities: list[GraphNodeIdentity],
    edges: list[GraphEdge],
) -> GovernanceGraph:
    return GovernanceGraph.from_parts([_node(identity) for identity in identities], edges)


def _path_targets(result: GovernanceImpactResult) -> list[str]:
    return [path.target.logical_id for path in result.paths]


# --- I. Basic traversal ---


def test_empty_changed_nodes_raises() -> None:
    a = _table("a")
    graph = _graph([a], [])
    with pytest.raises(ValueError, match="changed_nodes must not be empty"):
        analyze_downstream_impact(graph, [])


def test_missing_root_raises() -> None:
    a = _table("a")
    missing = _table("missing")
    graph = _graph([a], [])
    with pytest.raises(ValueError, match="changed node not present in graph"):
        analyze_downstream_impact(graph, [missing])


def test_duplicate_roots_dedup() -> None:
    a = _table("a")
    b = _table("b")
    graph = _graph([a, b], [_depends(b, a)])
    result = analyze_downstream_impact(graph, [a, a, a])
    assert result.changed_nodes == (a,)
    assert result.direct_nodes == (b,)


def test_root_permutation_invariance() -> None:
    a = _table("a")
    b = _table("b")
    c = _table("c")
    graph = _graph([a, b, c], [_depends(c, a), _depends(c, b)])
    r1 = analyze_downstream_impact(graph, [a, b])
    r2 = analyze_downstream_impact(graph, [b, a])
    assert r1.to_dict() == r2.to_dict()


def test_isolated_root_empty_impact() -> None:
    a = _table("a")
    graph = _graph([a], [])
    result = analyze_downstream_impact(graph, [a])
    assert result.changed_nodes == (a,)
    assert result.direct_nodes == ()
    assert result.transitive_nodes == ()
    assert result.affected_edges == ()
    assert result.paths == ()


def test_single_depends_on_downstream_hop() -> None:
    a = _table("a")
    b = _table("b")
    edge = _depends(b, a)
    graph = _graph([a, b], [edge])
    result = analyze_downstream_impact(graph, [a])
    assert result.direct_nodes == (b,)
    assert result.transitive_nodes == ()
    assert result.affected_edges == (edge,)
    assert len(result.paths) == 1
    path = result.paths[0]
    assert path.root == a
    assert path.target == b
    assert path.distance == 1
    assert path.steps[0].traversal == "reverse"
    assert path.steps[0].edge == edge


def test_chain_direct_vs_transitive() -> None:
    a = _table("a")
    b = _table("b")
    c = _table("c")
    graph = _graph([a, b, c], [_depends(b, a), _depends(c, b)])
    result = analyze_downstream_impact(graph, [a])
    assert result.direct_nodes == (b,)
    assert result.transitive_nodes == (c,)
    assert _path_targets(result) == ["b", "c"]
    c_path = next(path for path in result.paths if path.target == c)
    assert c_path.distance == 2
    assert [step.to_node for step in c_path.steps] == [b, c]


def test_branch_one_upstream_multiple_downstream() -> None:
    a = _table("a")
    b = _table("b")
    c = _table("c")
    graph = _graph([a, b, c], [_depends(b, a), _depends(c, a)])
    result = analyze_downstream_impact(graph, [a])
    assert set(result.direct_nodes) == {b, c}
    assert result.transitive_nodes == ()


def test_merge_multiple_upstream_same_downstream_dedup() -> None:
    a = _table("a")
    b = _table("b")
    x = _table("x")
    graph = _graph([a, b, x], [_depends(x, a), _depends(x, b)])
    result = analyze_downstream_impact(graph, [a, b])
    assert result.direct_nodes == (x,)
    assert len(result.paths) == 1


def test_table_level_depends_on() -> None:
    upstream = _table("orders")
    downstream = _table("orders_enriched")
    graph = _graph([upstream, downstream], [_depends(downstream, upstream)])
    result = analyze_downstream_impact(graph, [upstream])
    assert result.direct_nodes == (downstream,)


def test_column_level_depends_on() -> None:
    t_in = _table("orders")
    t_out = _table("orders_enriched")
    col_in = _column("amount", t_in)
    col_out = _column("amount", t_out)
    graph = _graph(
        [t_in, t_out, col_in, col_out],
        [
            _contains(t_in, col_in),
            _contains(t_out, col_out),
            _depends(col_out, col_in),
        ],
    )
    result = analyze_downstream_impact(graph, [col_in])
    assert result.direct_nodes == (col_out,)
    assert result.paths[0].steps[0].traversal == "reverse"


def test_mixed_table_column_graph_deterministic() -> None:
    t_a = _table("a")
    t_b = _table("b")
    col_a = _column("c", t_a)
    col_b = _column("c", t_b)
    edges = [
        _contains(t_a, col_a),
        _contains(t_b, col_b),
        _depends(t_b, t_a),
        _depends(col_b, col_a),
    ]
    g1 = _graph([t_a, t_b, col_a, col_b], edges)
    g2 = _graph([col_b, col_a, t_b, t_a], list(reversed(edges)))
    assert (
        analyze_downstream_impact(g1, [t_a, col_a]).to_dict()
        == analyze_downstream_impact(g2, [col_a, t_a]).to_dict()
    )


# --- II. Direction semantics ---


def test_depends_on_reverse_only() -> None:
    a = _table("a")
    b = _table("b")
    # stored: b depends_on a  => changing b must NOT mark a downstream
    graph = _graph([a, b], [_depends(b, a)])
    result = analyze_downstream_impact(graph, [b])
    assert result.direct_nodes == ()
    assert result.transitive_nodes == ()


def test_contains_forward() -> None:
    table = _table("orders")
    col = _column("id", table)
    graph = _graph([table, col], [_contains(table, col)])
    result = analyze_downstream_impact(graph, [table])
    assert result.direct_nodes == (col,)
    assert result.paths[0].steps[0].traversal == "forward"


def test_contains_reverse_not_traversed() -> None:
    table = _table("orders")
    col = _column("id", table)
    sibling = _column("name", table)
    graph = _graph([table, col, sibling], [_contains(table, col), _contains(table, sibling)])
    result = analyze_downstream_impact(graph, [col])
    assert table not in result.direct_nodes
    assert table not in result.transitive_nodes
    assert sibling not in result.direct_nodes
    assert sibling not in result.transitive_nodes


def test_governs_forward() -> None:
    contract = _id(NODE_KIND_CONTRACT, "orders_contract")
    dataset = _id(NODE_KIND_DATASET, "orders")
    graph = _graph([contract, dataset], [_governs(contract, dataset)])
    result = analyze_downstream_impact(graph, [contract])
    assert result.direct_nodes == (dataset,)
    assert result.paths[0].steps[0].traversal == "forward"


def test_governs_reverse_not_traversed() -> None:
    contract = _id(NODE_KIND_CONTRACT, "orders_contract")
    dataset = _id(NODE_KIND_DATASET, "orders")
    graph = _graph([contract, dataset], [_governs(contract, dataset)])
    result = analyze_downstream_impact(graph, [dataset])
    assert contract not in result.direct_nodes
    assert contract not in result.transitive_nodes


def test_unknown_edge_kind_ignored() -> None:
    a = _table("a")
    b = _table("b")
    unknown = GraphEdge(source=a, target=b, kind="related_to")
    graph = _graph([a, b], [unknown])
    result = analyze_downstream_impact(graph, [a])
    assert result.direct_nodes == ()
    assert result.affected_edges == ()


# --- III. Determinism ---


def test_shuffled_nodes_edges_same_result() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    edges = [_depends(b, a), _depends(c, b)]
    payloads = []
    for node_order in itertools.permutations([a, b, c]):
        for edge_order in itertools.permutations(edges):
            graph = _graph(list(node_order), list(edge_order))
            payloads.append(repr(analyze_downstream_impact(graph, [a]).to_dict()))
    assert len(set(payloads)) == 1


def test_shuffled_changed_roots_same_result() -> None:
    a, b, x = _table("a"), _table("b"), _table("x")
    graph = _graph([a, b, x], [_depends(x, a), _depends(x, b)])
    payloads = {
        repr(analyze_downstream_impact(graph, list(order)).to_dict())
        for order in itertools.permutations([a, b])
    }
    assert len(payloads) == 1


def test_duplicate_equivalent_edges_normalized_same() -> None:
    a, b = _table("a"), _table("b")
    p1 = _prov("dbt", "m1")
    p2 = _prov("openlineage", "r1")
    e1 = GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON, provenance=(p1,))
    e2 = GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON, provenance=(p2,))
    g1 = GovernanceGraph.from_parts([_node(a), _node(b)], [e1, e2])
    g2 = GovernanceGraph.from_parts([_node(b), _node(a)], [e2, e1])
    assert (
        analyze_downstream_impact(g1, [a]).to_dict() == analyze_downstream_impact(g2, [a]).to_dict()
    )


def test_diamond_canonical_shortest_path_stable() -> None:
    root = _table("root")
    left = _table("left")
    right = _table("right")
    sink = _table("sink")
    graph = _graph(
        [root, left, right, sink],
        [
            _depends(left, root),
            _depends(right, root),
            _depends(sink, left),
            _depends(sink, right),
        ],
    )
    result = analyze_downstream_impact(graph, [root])
    sink_path = next(path for path in result.paths if path.target == sink)
    assert sink_path.distance == 2
    # Lexicographically smaller path by path_sort_key: compare both candidates.
    via_left = ImpactPath(
        root=root,
        target=sink,
        steps=(
            ImpactStep(root, left, _depends(left, root), "reverse"),
            ImpactStep(left, sink, _depends(sink, left), "reverse"),
        ),
    )
    via_right = ImpactPath(
        root=root,
        target=sink,
        steps=(
            ImpactStep(root, right, _depends(right, root), "reverse"),
            ImpactStep(right, sink, _depends(sink, right), "reverse"),
        ),
    )
    from governance.domain.impact import _path_sort_key

    expected = via_left if _path_sort_key(via_left) < _path_sort_key(via_right) else via_right
    assert [step.to_node for step in sink_path.steps] == [step.to_node for step in expected.steps]
    # Alternate diamond edges remain in affected_edges.
    assert len(result.affected_edges) == 4


def test_equal_distance_multi_root_canonical_path() -> None:
    a = _table("a")
    b = _table("b")
    x = _table("x")
    # distance 1 from both roots
    graph = _graph([a, b, x], [_depends(x, a), _depends(x, b)])
    result = analyze_downstream_impact(graph, [a, b])
    assert result.direct_nodes == (x,)
    path = result.paths[0]
    assert path.distance == 1
    via_a = ImpactPath(
        root=a,
        target=x,
        steps=(ImpactStep(a, x, _depends(x, a), "reverse"),),
    )
    via_b = ImpactPath(
        root=b,
        target=x,
        steps=(ImpactStep(b, x, _depends(x, b), "reverse"),),
    )
    from governance.domain.impact import _path_sort_key

    expected = via_a if _path_sort_key(via_a) < _path_sort_key(via_b) else via_b
    assert path.root == expected.root
    assert path.steps[0].edge.logical_identity() == expected.steps[0].edge.logical_identity()


def test_multi_root_minimum_distance_wins() -> None:
    a = _table("a")
    b = _table("b")
    mid = _table("mid")
    x = _table("x")
    # A -> mid -> x (dist 2), B -> x (dist 1)
    graph = _graph(
        [a, b, mid, x],
        [_depends(mid, a), _depends(x, mid), _depends(x, b)],
    )
    result = analyze_downstream_impact(graph, [a, b])
    assert x in result.direct_nodes
    assert x not in result.transitive_nodes
    path = next(path for path in result.paths if path.target == x)
    assert path.root == b
    assert path.distance == 1


def test_provenance_order_does_not_alter_reachability() -> None:
    a, b = _table("a"), _table("b")
    p1, p2 = _prov("dbt", "a"), _prov("dbt", "b")
    e1 = GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON, provenance=(p1, p2))
    e2 = GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON, provenance=(p2, p1))
    g1 = _graph([a, b], [e1])
    g2 = _graph([a, b], [e2])
    r1 = analyze_downstream_impact(g1, [a])
    r2 = analyze_downstream_impact(g2, [a])
    assert r1.direct_nodes == r2.direct_nodes == (b,)
    assert r1.paths[0].target == r2.paths[0].target == b


def test_repeated_execution_same_output() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    graph = _graph([a, b, c], [_depends(b, a), _depends(c, b)])
    assert (
        analyze_downstream_impact(graph, [a]).to_dict()
        == analyze_downstream_impact(graph, [a]).to_dict()
    )


# --- IV. Cycles ---


def test_two_node_depends_on_cycle_terminates() -> None:
    a, b = _table("a"), _table("b")
    e_ab = _depends(a, b)
    e_ba = _depends(b, a)
    graph = _graph([a, b], [e_ab, e_ba])
    result = analyze_downstream_impact(graph, [a])
    assert result.direct_nodes == (b,)
    assert a not in result.direct_nodes
    assert a not in result.transitive_nodes
    assert set(result.affected_edges) == {e_ab, e_ba}


def test_three_node_cycle_terminates() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    graph = _graph(
        [a, b, c],
        [_depends(b, a), _depends(c, b), _depends(a, c)],
    )
    result = analyze_downstream_impact(graph, [a])
    assert set(result.direct_nodes) | set(result.transitive_nodes) == {b, c}
    assert a not in result.direct_nodes
    assert a not in result.transitive_nodes


def test_cycle_back_to_root_does_not_mark_root_affected() -> None:
    a, b = _table("a"), _table("b")
    graph = _graph([a, b], [_depends(b, a), _depends(a, b)])
    result = analyze_downstream_impact(graph, [a])
    assert result.changed_nodes == (a,)
    assert result.direct_nodes == (b,)
    assert a not in result.direct_nodes


def test_cycle_plus_downstream_branch_reaches_once() -> None:
    a, b, c, d = _table("a"), _table("b"), _table("c"), _table("d")
    graph = _graph(
        [a, b, c, d],
        [
            _depends(b, a),
            _depends(a, b),
            _depends(c, b),
            _depends(d, c),
        ],
    )
    result = analyze_downstream_impact(graph, [a])
    assert b in result.direct_nodes
    assert c in result.transitive_nodes or c in result.direct_nodes
    assert d in result.transitive_nodes
    assert len([path for path in result.paths if path.target == d]) == 1


def test_affected_edges_include_reachable_cycle_and_alternate() -> None:
    root = _table("root")
    left = _table("left")
    right = _table("right")
    sink = _table("sink")
    edges = [
        _depends(left, root),
        _depends(right, root),
        _depends(sink, left),
        _depends(sink, right),
        _depends(left, right),  # alternate / cycle-ish within subgraph
    ]
    graph = _graph([root, left, right, sink], edges)
    result = analyze_downstream_impact(graph, [root])
    assert set(result.affected_edges) == set(edges)


def test_iterative_traversal_no_recursion_risk() -> None:
    nodes = [_table(f"n{i:03d}") for i in range(50)]
    edges = [_depends(nodes[i + 1], nodes[i]) for i in range(49)]
    graph = _graph(nodes, edges)
    result = analyze_downstream_impact(graph, [nodes[0]])
    assert len(result.direct_nodes) == 1
    assert len(result.transitive_nodes) == 48
    assert result.paths[-1].distance == 49


# --- VIII. Paths / edges ---


def test_one_path_per_affected_node() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    graph = _graph([a, b, c], [_depends(b, a), _depends(c, a)])
    result = analyze_downstream_impact(graph, [a])
    targets = [path.target for path in result.paths]
    assert len(targets) == len(set(targets))
    assert set(targets) == set(result.direct_nodes) | set(result.transitive_nodes)


def test_path_distance_matches_direct_transitive() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    graph = _graph([a, b, c], [_depends(b, a), _depends(c, b)])
    result = analyze_downstream_impact(graph, [a])
    for path in result.paths:
        if path.target in result.direct_nodes:
            assert path.distance == 1
        else:
            assert path.distance >= 2


def test_path_steps_contiguous() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    graph = _graph([a, b, c], [_depends(b, a), _depends(c, b)])
    result = analyze_downstream_impact(graph, [a])
    for path in result.paths:
        assert path.steps[0].from_node == path.root
        assert path.steps[-1].to_node == path.target
        for i in range(len(path.steps) - 1):
            assert path.steps[i].to_node == path.steps[i + 1].from_node


def test_impact_step_exposes_stored_edge_and_traversal() -> None:
    a, b = _table("a"), _table("b")
    edge = _depends(b, a)
    graph = _graph([a, b], [edge])
    step = analyze_downstream_impact(graph, [a]).paths[0].steps[0]
    assert step.edge == edge
    assert step.traversal == "reverse"
    assert step.from_node == a
    assert step.to_node == b
    payload = step.to_dict()
    assert payload["traversal"] == "reverse"
    assert payload["edge"]["kind"] == EDGE_KIND_DEPENDS_ON


def test_affected_edges_include_alternate_beyond_canonical_path() -> None:
    root = _table("root")
    left = _table("left")
    right = _table("right")
    sink = _table("sink")
    edges = [
        _depends(left, root),
        _depends(right, root),
        _depends(sink, left),
        _depends(sink, right),
    ]
    result = analyze_downstream_impact(_graph([root, left, right, sink], edges), [root])
    path_edges = {step.edge for path in result.paths for step in path.steps}
    assert path_edges < set(result.affected_edges) or len(result.affected_edges) == 4
    assert set(result.affected_edges) == set(edges)


def test_affected_edges_sorted_canonical() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    edges = [_depends(c, a), _depends(b, a)]
    result = analyze_downstream_impact(_graph([a, b, c], edges), [a])
    keys = [edge.logical_sort_key() for edge in result.affected_edges]
    assert keys == sorted(keys)


def test_path_serialization_canonical() -> None:
    a, b = _table("a"), _table("b")
    graph = _graph([a, b], [_depends(b, a)])
    payload = analyze_downstream_impact(graph, [a]).to_dict()
    assert list(payload.keys()) == sorted(payload.keys())
    assert "schema_version" not in payload
    path = payload["paths"][0]
    assert path["distance"] == 1
    assert list(path.keys()) == sorted(path.keys())


def test_impact_step_rejects_incompatible_traversal() -> None:
    a, b = _table("a"), _table("b")
    edge = _depends(b, a)
    with pytest.raises(ValueError, match="impact step traversal incompatible"):
        ImpactStep(from_node=a, to_node=b, edge=edge, traversal="forward")


# --- V. Containment blast radius ---


def test_dataset_table_change_propagates_to_descendants() -> None:
    ds = _id(NODE_KIND_DATASET, "sales")
    table = _table("orders")
    col = _column("id", table)
    # parent chain: dataset is not automatic parent of table unless identity.parent set.
    # Use contains edges for hierarchy propagation.
    graph = _graph([ds, table, col], [_contains(ds, table), _contains(table, col)])
    result = analyze_downstream_impact(graph, [ds])
    assert table in result.direct_nodes
    assert col in result.transitive_nodes


def test_column_change_does_not_promote_parent_or_sibling() -> None:
    table = _table("orders")
    col_a = _column("a", table)
    col_b = _column("b", table)
    graph = _graph([table, col_a, col_b], [_contains(table, col_a), _contains(table, col_b)])
    result = analyze_downstream_impact(graph, [col_a])
    assert result.direct_nodes == ()
    assert result.transitive_nodes == ()
    assert table in result.governance_assets
    assert col_a in result.governance_assets
    assert col_b not in result.governance_assets


def test_nested_column_parents_in_governance_assets() -> None:
    source = _id(NODE_KIND_DATA_SOURCE, "pg")
    dataset = GraphNodeIdentity(NS, NODE_KIND_DATASET, "sales", parent=source)
    table = GraphNodeIdentity(NS, NODE_KIND_TABLE, "orders", parent=dataset)
    col = GraphNodeIdentity(NS, NODE_KIND_COLUMN, "id", parent=table)
    graph = _graph(
        [source, dataset, table, col],
        [_contains(source, dataset), _contains(dataset, table), _contains(table, col)],
    )
    result = analyze_downstream_impact(graph, [col])
    assert set(result.governance_assets) >= {col, table, dataset, source}


def test_container_child_downstream_column_path_explainable() -> None:
    t_in = _table("orders")
    t_out = _table("orders_enriched")
    col_in = _column("amount", t_in)
    col_out = _column("amount", t_out)
    graph = _graph(
        [t_in, t_out, col_in, col_out],
        [
            _contains(t_in, col_in),
            _contains(t_out, col_out),
            _depends(col_out, col_in),
        ],
    )
    result = analyze_downstream_impact(graph, [t_in])
    assert col_in in result.direct_nodes
    assert col_out in result.transitive_nodes
    path = next(path for path in result.paths if path.target == col_out)
    assert [step.traversal for step in path.steps] == ["forward", "reverse"]
    assert [step.to_node for step in path.steps] == [col_in, col_out]


# --- VI. Contracts ---


def test_contract_root_governs_dataset_direct() -> None:
    contract = _id(NODE_KIND_CONTRACT, "orders_v1")
    dataset = _id(NODE_KIND_DATASET, "orders")
    col = GraphNodeIdentity(NS, NODE_KIND_COLUMN, "id", parent=dataset)
    graph = _graph(
        [contract, dataset, col],
        [_governs(contract, dataset), _contains(dataset, col)],
    )
    result = analyze_downstream_impact(graph, [contract])
    assert result.direct_nodes == (dataset,)
    assert col in result.transitive_nodes
    assert contract in result.associated_contracts


def test_contract_root_continues_into_downstream_lineage() -> None:
    contract = _id(NODE_KIND_CONTRACT, "orders_v1")
    dataset = _id(NODE_KIND_DATASET, "orders")
    col_in = GraphNodeIdentity(NS, NODE_KIND_COLUMN, "amount", parent=dataset)
    t_out = _table("mart")
    col_out = _column("amount", t_out)
    graph = _graph(
        [contract, dataset, col_in, t_out, col_out],
        [
            _governs(contract, dataset),
            _contains(dataset, col_in),
            _contains(t_out, col_out),
            _depends(col_out, col_in),
        ],
    )
    result = analyze_downstream_impact(graph, [contract])
    assert col_out in result.transitive_nodes
    assert contract in result.associated_contracts


def test_dataset_root_governing_contract_associated_only() -> None:
    contract = _id(NODE_KIND_CONTRACT, "orders_v1")
    dataset = _id(NODE_KIND_DATASET, "orders")
    graph = _graph([contract, dataset], [_governs(contract, dataset)])
    result = analyze_downstream_impact(graph, [dataset])
    assert contract not in result.direct_nodes
    assert contract not in result.transitive_nodes
    assert result.associated_contracts == (contract,)


def test_affected_column_associates_ancestor_governing_contract() -> None:
    contract = _id(NODE_KIND_CONTRACT, "orders_v1")
    dataset = _id(NODE_KIND_DATASET, "orders")
    table = GraphNodeIdentity(NS, NODE_KIND_TABLE, "orders_tbl", parent=dataset)
    col = GraphNodeIdentity(NS, NODE_KIND_COLUMN, "id", parent=table)
    graph = _graph(
        [contract, dataset, table, col],
        [
            _governs(contract, dataset),
            _contains(dataset, table),
            _contains(table, col),
        ],
    )
    result = analyze_downstream_impact(graph, [col])
    assert result.associated_contracts == (contract,)
    assert dataset in result.governance_assets
    assert table in result.governance_assets


def test_multiple_contracts_dedup_and_canonical_sort() -> None:
    c1 = _id(NODE_KIND_CONTRACT, "contract_a")
    c2 = _id(NODE_KIND_CONTRACT, "contract_b")
    dataset = _id(NODE_KIND_DATASET, "orders")
    graph = _graph(
        [c1, c2, dataset],
        [_governs(c1, dataset), _governs(c2, dataset)],
    )
    result = analyze_downstream_impact(graph, [dataset])
    assert result.associated_contracts == tuple(
        sorted((c1, c2), key=lambda node: node.canonical_bytes())
    )


def test_contract_association_does_not_alter_distances() -> None:
    contract = _id(NODE_KIND_CONTRACT, "orders_v1")
    dataset = _id(NODE_KIND_DATASET, "orders")
    downstream = _table("mart")
    graph = _graph(
        [contract, dataset, downstream],
        [_governs(contract, dataset), _depends(downstream, dataset)],
    )
    result = analyze_downstream_impact(graph, [dataset])
    assert result.direct_nodes == (downstream,)
    assert result.transitive_nodes == ()
    assert result.associated_contracts == (contract,)
    assert contract not in result.direct_nodes


def test_downstream_output_associates_governing_contract() -> None:
    contract = _id(NODE_KIND_CONTRACT, "mart_contract")
    dataset = _id(NODE_KIND_DATASET, "mart")
    table = GraphNodeIdentity(NS, NODE_KIND_TABLE, "mart_tbl", parent=dataset)
    upstream = _table("orders")
    graph = _graph(
        [contract, dataset, table, upstream],
        [
            _governs(contract, dataset),
            _contains(dataset, table),
            _depends(table, upstream),
        ],
    )
    result = analyze_downstream_impact(graph, [upstream])
    assert table in result.direct_nodes
    assert contract in result.associated_contracts


# --- VII. Context ---


def test_governance_assets_include_impact_core_non_contracts() -> None:
    a, b = _table("a"), _table("b")
    graph = _graph([a, b], [_depends(b, a)])
    result = analyze_downstream_impact(graph, [a])
    assert set(result.governance_assets) >= {a, b}


def test_governance_assets_include_ancestors_as_context() -> None:
    table = _table("orders")
    col = _column("id", table)
    downstream = _table("mart")
    graph = _graph(
        [table, col, downstream],
        [_contains(table, col), _depends(downstream, col)],
    )
    result = analyze_downstream_impact(graph, [col])
    assert downstream in result.direct_nodes
    assert table in result.governance_assets
    assert table not in result.direct_nodes
    assert table not in result.transitive_nodes


def test_context_ancestors_do_not_open_traversal() -> None:
    table = _table("orders")
    col_a = _column("a", table)
    col_b = _column("b", table)
    # Sibling reachable only via reverse-contains through parent — must NOT happen.
    graph = _graph([table, col_a, col_b], [_contains(table, col_a), _contains(table, col_b)])
    result = analyze_downstream_impact(graph, [col_a])
    assert col_b not in result.direct_nodes
    assert col_b not in result.transitive_nodes
    assert table in result.governance_assets


def test_policy_relevant_nodes_structural_subset() -> None:
    source = _id(NODE_KIND_DATA_SOURCE, "pg")
    dataset = GraphNodeIdentity(NS, NODE_KIND_DATASET, "sales", parent=source)
    table = GraphNodeIdentity(NS, NODE_KIND_TABLE, "orders", parent=dataset)
    col = GraphNodeIdentity(NS, NODE_KIND_COLUMN, "id", parent=table)
    graph = _graph(
        [source, dataset, table, col],
        [_contains(source, dataset), _contains(dataset, table), _contains(table, col)],
    )
    result = analyze_downstream_impact(graph, [col])
    kinds = {node.kind for node in result.policy_relevant_nodes}
    assert kinds <= {
        NODE_KIND_DATA_SOURCE,
        NODE_KIND_DATASET,
        NODE_KIND_TABLE,
        NODE_KIND_COLUMN,
    }
    assert set(result.policy_relevant_nodes) >= {source, dataset, table, col}


def test_contract_excluded_from_policy_relevant() -> None:
    contract = _id(NODE_KIND_CONTRACT, "orders_v1")
    dataset = _id(NODE_KIND_DATASET, "orders")
    graph = _graph([contract, dataset], [_governs(contract, dataset)])
    result = analyze_downstream_impact(graph, [contract])
    assert contract in result.associated_contracts
    assert contract not in result.policy_relevant_nodes
    assert contract not in result.governance_assets


def test_transformation_excluded_from_policy_relevant_but_may_be_governance_asset() -> None:
    transform = _id(NODE_KIND_TRANSFORMATION, "orders_xf")
    downstream = _table("mart")
    graph = _graph([transform, downstream], [_depends(downstream, transform)])
    result = analyze_downstream_impact(graph, [transform])
    assert transform in result.governance_assets
    assert transform not in result.policy_relevant_nodes
    assert downstream in result.policy_relevant_nodes


def test_associated_contracts_separate_from_governance_assets() -> None:
    contract = _id(NODE_KIND_CONTRACT, "orders_v1")
    dataset = _id(NODE_KIND_DATASET, "orders")
    graph = _graph([contract, dataset], [_governs(contract, dataset)])
    result = analyze_downstream_impact(graph, [dataset])
    assert contract in result.associated_contracts
    assert contract not in result.governance_assets
    assert dataset in result.governance_assets


# --- GovernanceImpactResult constructor normalization ---


def _sample_path(root: GraphNodeIdentity, target: GraphNodeIdentity) -> ImpactPath:
    edge = _depends(target, root)
    return ImpactPath(
        root=root,
        target=target,
        steps=(ImpactStep(root, target, edge, "reverse"),),
    )


def test_result_constructor_converts_lists_to_tuples() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    edge = _depends(b, a)
    path = _sample_path(a, b)
    result = GovernanceImpactResult(
        changed_nodes=[a],
        direct_nodes=[b],
        transitive_nodes=[c],
        affected_edges=[edge],
        paths=[path],
        associated_contracts=[],
        governance_assets=[a, b, c],
        policy_relevant_nodes=[a, b, c],
    )
    assert isinstance(result.changed_nodes, tuple)
    assert isinstance(result.direct_nodes, tuple)
    assert isinstance(result.transitive_nodes, tuple)
    assert isinstance(result.affected_edges, tuple)
    assert isinstance(result.paths, tuple)
    assert isinstance(result.associated_contracts, tuple)
    assert isinstance(result.governance_assets, tuple)
    assert isinstance(result.policy_relevant_nodes, tuple)


def test_result_constructor_canonical_ordering() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    ordered = tuple(sorted((a, b, c), key=lambda node: node.canonical_bytes()))
    result = GovernanceImpactResult(
        changed_nodes=[c, b, a],
        direct_nodes=[c, a, b],
        transitive_nodes=[b, c, a],
        affected_edges=[],
        paths=[],
        associated_contracts=[c, a],
        governance_assets=[b, a, c],
        policy_relevant_nodes=[c, b, a],
    )
    assert result.changed_nodes == ordered
    assert result.direct_nodes == ordered
    assert result.transitive_nodes == ordered
    assert result.associated_contracts == tuple(
        sorted((a, c), key=lambda node: node.canonical_bytes())
    )
    assert result.governance_assets == ordered
    assert result.policy_relevant_nodes == ordered


def test_result_constructor_dedups_node_identities() -> None:
    a, b = _table("a"), _table("b")
    result = GovernanceImpactResult(
        changed_nodes=[a, a, a],
        direct_nodes=[b, b],
        transitive_nodes=[a, b, a],
        affected_edges=[],
        paths=[],
        associated_contracts=[a, a],
        governance_assets=[b, a, b],
        policy_relevant_nodes=[a, b, a],
    )
    assert result.changed_nodes == (a,)
    assert result.direct_nodes == (b,)
    assert result.transitive_nodes == tuple(sorted((a, b), key=lambda n: n.canonical_bytes()))
    assert result.associated_contracts == (a,)


def test_result_constructor_affected_edges_dedup_and_sort() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    e1 = _depends(b, a)
    e2 = _depends(c, a)
    result = GovernanceImpactResult(
        changed_nodes=[a],
        direct_nodes=[b, c],
        transitive_nodes=[],
        affected_edges=[e2, e1, e2, e1],
        paths=[],
        associated_contracts=[],
        governance_assets=[a, b, c],
        policy_relevant_nodes=[a, b, c],
    )
    assert result.affected_edges == tuple(
        sorted((e1, e2), key=lambda edge: edge.logical_sort_key())
    )


def test_result_constructor_affected_edges_conflict_raises() -> None:
    a, b = _table("a"), _table("b")
    e1 = GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON, attributes={"k": 1})
    e2 = GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON, attributes={"k": 2})
    with pytest.raises(ValueError, match="conflicting GraphEdge attributes"):
        GovernanceImpactResult(
            changed_nodes=[a],
            direct_nodes=[b],
            transitive_nodes=[],
            affected_edges=[e1, e2],
            paths=[],
            associated_contracts=[],
            governance_assets=[a, b],
            policy_relevant_nodes=[a, b],
        )


def test_result_constructor_affected_edges_unions_provenance() -> None:
    a, b = _table("a"), _table("b")
    prov_a = _prov("dbt", "model_a")
    prov_b = _prov("openlineage", "run_b")
    e1 = GraphEdge(
        source=b, target=a, kind=EDGE_KIND_DEPENDS_ON, attributes={}, provenance=(prov_a,)
    )
    e2 = GraphEdge(
        source=b, target=a, kind=EDGE_KIND_DEPENDS_ON, attributes={}, provenance=(prov_b,)
    )
    result = GovernanceImpactResult(
        changed_nodes=[a],
        direct_nodes=[b],
        transitive_nodes=[],
        affected_edges=[e1, e2],
        paths=[],
        associated_contracts=[],
        governance_assets=[a, b],
        policy_relevant_nodes=[a, b],
    )
    assert len(result.affected_edges) == 1
    expected = tuple(sorted((prov_a, prov_b), key=lambda record: record.canonical_sort_key()))
    assert result.affected_edges[0].provenance == expected


def test_result_constructor_affected_edges_provenance_order_invariant() -> None:
    a, b = _table("a"), _table("b")
    e1 = GraphEdge(
        source=b,
        target=a,
        kind=EDGE_KIND_DEPENDS_ON,
        attributes={},
        provenance=(_prov("dbt", "model_a"),),
    )
    e2 = GraphEdge(
        source=b,
        target=a,
        kind=EDGE_KIND_DEPENDS_ON,
        attributes={},
        provenance=(_prov("openlineage", "run_b"),),
    )
    kwargs = dict(
        changed_nodes=[a],
        direct_nodes=[b],
        transitive_nodes=[],
        paths=[],
        associated_contracts=[],
        governance_assets=[a, b],
        policy_relevant_nodes=[a, b],
    )
    r1 = GovernanceImpactResult(affected_edges=[e1, e2], **kwargs)
    r2 = GovernanceImpactResult(affected_edges=[e2, e1], **kwargs)
    assert r1.to_dict() == r2.to_dict()


def test_result_constructor_affected_edges_exact_duplicate_provenance_once() -> None:
    a, b = _table("a"), _table("b")
    prov = _prov("dbt", "model_a")
    edge = GraphEdge(
        source=b, target=a, kind=EDGE_KIND_DEPENDS_ON, attributes={}, provenance=(prov,)
    )
    result = GovernanceImpactResult(
        changed_nodes=[a],
        direct_nodes=[b],
        transitive_nodes=[],
        affected_edges=[edge, edge],
        paths=[],
        associated_contracts=[],
        governance_assets=[a, b],
        policy_relevant_nodes=[a, b],
    )
    assert len(result.affected_edges) == 1
    assert result.affected_edges[0].provenance == (prov,)


def test_result_constructor_paths_sorted_by_target() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    path_b = _sample_path(a, b)
    path_c = _sample_path(a, c)
    expected = tuple(sorted((path_b, path_c), key=lambda path: path.target.canonical_bytes()))
    result = GovernanceImpactResult(
        changed_nodes=[a],
        direct_nodes=[b, c],
        transitive_nodes=[],
        affected_edges=[],
        paths=[path_c, path_b],
        associated_contracts=[],
        governance_assets=[a, b, c],
        policy_relevant_nodes=[a, b, c],
    )
    assert result.paths == expected


def test_result_constructor_duplicate_path_target_raises() -> None:
    a, b = _table("a"), _table("b")
    path = _sample_path(a, b)
    with pytest.raises(ValueError, match="duplicate impact path target"):
        GovernanceImpactResult(
            changed_nodes=[a],
            direct_nodes=[b],
            transitive_nodes=[],
            affected_edges=[],
            paths=[path, path],
            associated_contracts=[],
            governance_assets=[a, b],
            policy_relevant_nodes=[a, b],
        )


def test_result_constructor_invalid_element_types() -> None:
    a, b = _table("a"), _table("b")
    path = _sample_path(a, b)
    edge = _depends(b, a)
    with pytest.raises(TypeError, match="changed_nodes entries must be GraphNodeIdentity"):
        GovernanceImpactResult(
            changed_nodes=[a, "bad"],  # type: ignore[list-item]
            direct_nodes=[],
            transitive_nodes=[],
            affected_edges=[],
            paths=[],
            associated_contracts=[],
            governance_assets=[],
            policy_relevant_nodes=[],
        )
    with pytest.raises(TypeError, match="affected_edges entries must be GraphEdge"):
        GovernanceImpactResult(
            changed_nodes=[a],
            direct_nodes=[],
            transitive_nodes=[],
            affected_edges=[edge, "bad"],  # type: ignore[list-item]
            paths=[],
            associated_contracts=[],
            governance_assets=[],
            policy_relevant_nodes=[],
        )
    with pytest.raises(TypeError, match="paths entries must be ImpactPath"):
        GovernanceImpactResult(
            changed_nodes=[a],
            direct_nodes=[b],
            transitive_nodes=[],
            affected_edges=[],
            paths=[path, "bad"],  # type: ignore[list-item]
            associated_contracts=[],
            governance_assets=[],
            policy_relevant_nodes=[],
        )


def test_analyze_result_stable_after_constructor_normalization() -> None:
    a, b, c = _table("a"), _table("b"), _table("c")
    graph = _graph([a, b, c], [_depends(b, a), _depends(c, b)])
    analyzed = analyze_downstream_impact(graph, [a])
    rebuilt = GovernanceImpactResult(
        changed_nodes=list(reversed(analyzed.changed_nodes)),
        direct_nodes=list(reversed(analyzed.direct_nodes)),
        transitive_nodes=list(reversed(analyzed.transitive_nodes)),
        affected_edges=list(reversed(analyzed.affected_edges)),
        paths=list(reversed(analyzed.paths)),
        associated_contracts=list(reversed(analyzed.associated_contracts)),
        governance_assets=list(reversed(analyzed.governance_assets)),
        policy_relevant_nodes=list(reversed(analyzed.policy_relevant_nodes)),
    )
    assert rebuilt.to_dict() == analyzed.to_dict()
