"""Deterministic downstream blast-radius / impact analysis."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from governance.domain.graph import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    EDGE_KIND_GOVERNS,
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    GovernanceGraph,
    GraphEdge,
    GraphNode,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.impact import (
    GovernanceImpactResult,
    ImpactPath,
    ImpactStep,
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
    assert analyze_downstream_impact(g1, [t_a, col_a]).to_dict() == analyze_downstream_impact(
        g2, [col_a, t_a]
    ).to_dict()


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
    assert analyze_downstream_impact(g1, [a]).to_dict() == analyze_downstream_impact(
        g2, [a]
    ).to_dict()


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
    assert [step.to_node for step in sink_path.steps] == [
        step.to_node for step in expected.steps
    ]
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
    assert analyze_downstream_impact(graph, [a]).to_dict() == analyze_downstream_impact(
        graph, [a]
    ).to_dict()


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
