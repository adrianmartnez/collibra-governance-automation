"""Unit tests for the vendor-neutral governance graph foundation."""

from __future__ import annotations

import math
from typing import Any

import pytest

from governance.domain import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    EDGE_KIND_GOVERNS,
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_TABLE,
    GovernanceGraph,
    GraphEdge,
    GraphNode,
    GraphNodeIdentity,
    ProvenanceRecord,
)
from governance.domain.graph import _CanonicalArray, _canonicalize_json_value, _CanonicalObject

NS = "acme.commerce"


def _table_id(logical_id: str = "orders") -> GraphNodeIdentity:
    return GraphNodeIdentity(NS, NODE_KIND_TABLE, logical_id)


def _column_id(
    logical_id: str = "customer_id",
    parent: GraphNodeIdentity | None = None,
) -> GraphNodeIdentity:
    return GraphNodeIdentity(NS, NODE_KIND_COLUMN, logical_id, parent=parent or _table_id())


def _prov(
    provider: str,
    ref: str,
    version: str | None = None,
    mode: str = "observed",
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provider_type=provider,
        source_ref=ref,
        source_version=version,
        observation_mode=mode,
    )


def _node(
    identity: GraphNodeIdentity,
    name: str | None = None,
    *,
    description: str | None = None,
    attributes: dict[str, Any] | None = None,
    provenance: tuple[ProvenanceRecord, ...] = (),
) -> GraphNode:
    return GraphNode(
        identity=identity,
        name=name if name is not None else identity.logical_id,
        description=description,
        attributes=attributes,
        provenance=provenance,
    )


# --- A: Node identity ---


def test_node_identity_valid_construction() -> None:
    table = _table_id()
    column = _column_id(parent=table)
    assert table.namespace == NS
    assert column.parent == table
    assert column.to_canonical_list() == [
        NS,
        NODE_KIND_COLUMN,
        "customer_id",
        [NS, NODE_KIND_TABLE, "orders", None],
    ]


def test_node_identity_rejects_empty_fields() -> None:
    with pytest.raises(ValueError, match="namespace"):
        GraphNodeIdentity("", NODE_KIND_TABLE, "orders")
    with pytest.raises(ValueError, match="kind"):
        GraphNodeIdentity(NS, "  ", "orders")
    with pytest.raises(ValueError, match="logical_id"):
        GraphNodeIdentity(NS, NODE_KIND_TABLE, "")


def test_node_identity_canonical_stable_and_hashable() -> None:
    a = _table_id()
    b = GraphNodeIdentity(NS, NODE_KIND_TABLE, "orders")
    assert a == b
    assert hash(a) == hash(b)
    assert a is not b
    assert a.canonical_bytes() == b.canonical_bytes()


def test_node_identity_collision_safe_special_characters() -> None:
    cases = [
        ("a/b", "table", "x"),
        ("a.b", "table", "x"),
        ("a:b", "table", "x"),
        ("café", "table", "x"),
        ("a\x1fb", "table", "x"),
        ('a"b', "table", "x"),
        ("a", "table", "b/c"),
        ("a", "ta.ble", "x"),
    ]
    identities = [GraphNodeIdentity(ns, kind, lid) for ns, kind, lid in cases]
    canonicals = [identity.canonical_bytes() for identity in identities]
    assert len(set(canonicals)) == len(canonicals)


def test_column_identity_parent_aware_option_a() -> None:
    orders = _table_id("orders")
    items = _table_id("items")
    left = _column_id("id", parent=orders)
    right = _column_id("id", parent=items)
    assert left != right
    assert left.canonical_bytes() != right.canonical_bytes()
    assert _column_id("id", parent=orders) == left


def test_column_identity_requires_parent() -> None:
    with pytest.raises(ValueError, match="parent"):
        GraphNodeIdentity(NS, NODE_KIND_COLUMN, "customer_id")


def test_same_local_triple_distinct_with_different_parent() -> None:
    root = GraphNodeIdentity(NS, "folder", "x")
    child = GraphNodeIdentity(NS, "folder", "x", parent=root)
    assert child != root
    assert child.canonical_bytes() != root.canonical_bytes()
    assert child.parent == root


def test_open_kinds_allowed() -> None:
    custom = GraphNodeIdentity(NS, "semantic_view", "revenue")
    assert custom.kind == "semantic_view"


def test_namespace_is_logical_scope_not_provider() -> None:
    """Same logical asset uses one namespace; providers differ only in provenance."""
    identity = _table_id()
    node = _node(
        identity,
        provenance=(
            _prov("postgresql", "pg://demo/orders"),
            _prov("dbt", "model.acme.orders", "1.0", "declared"),
        ),
    )
    assert node.identity.namespace == NS
    assert node.identity.namespace not in {"postgresql", "dbt", "odcs", "openlineage"}
    assert {p.provider_type for p in node.provenance} == {"postgresql", "dbt"}


# --- B: Provenance ---


def test_provenance_deterministic_sort_with_none_versions() -> None:
    records = (
        _prov("dbt", "model.orders", "2"),
        _prov("dbt", "model.orders", None),
        _prov("dbt", "model.orders", "1"),
    )
    node = _node(_table_id(), provenance=records)
    # Total order via canonical_json_bytes (JSON null sorts after string values).
    versions = [record.source_version for record in node.provenance]
    assert versions == ["1", "2", None]


def test_provenance_permutation_same_order() -> None:
    a = (
        _prov("odcs", "contract://orders", "1", "declared"),
        _prov("postgresql", "pg://orders", None, "observed"),
        _prov("dbt", "model.orders", "2", "declared"),
    )
    b = tuple(reversed(a))
    node_a = _node(_table_id(), provenance=a)
    node_b = _node(_table_id(), provenance=b)
    assert node_a.provenance == node_b.provenance
    assert node_a.to_dict() == node_b.to_dict()


def test_provenance_exact_dedup() -> None:
    record = _prov("postgresql", "pg://orders")
    node = _node(_table_id(), provenance=(record, record))
    assert node.provenance == (record,)


# --- C: Attributes / nodes ---


def test_tagged_object_not_equal_array() -> None:
    obj = _canonicalize_json_value({"a": 1})
    arr = _canonicalize_json_value([["a", 1]])
    assert isinstance(obj, _CanonicalObject)
    assert isinstance(arr, _CanonicalArray)
    assert obj != arr
    n_obj = _node(_table_id("t_obj"), attributes={"v": {"a": 1}})
    n_arr = _node(_table_id("t_arr"), attributes={"v": [["a", 1]]})
    assert n_obj.to_dict()["attributes"] != n_arr.to_dict()["attributes"]
    assert n_obj.attributes_canonical != n_arr.attributes_canonical


def test_empty_object_root_vs_nested_empty_array() -> None:
    empty_obj = _node(_table_id("e1"), attributes={})
    with_empty_array = _node(_table_id("e2"), attributes={"v": []})
    assert empty_obj.to_dict()["attributes"] == {}
    assert with_empty_array.to_dict()["attributes"] == {"v": []}
    assert empty_obj.attributes_canonical != with_empty_array.attributes_canonical


def test_attributes_root_must_be_object() -> None:
    with pytest.raises(TypeError, match="object"):
        _node(_table_id(), attributes=["not", "an", "object"])  # type: ignore[arg-type]


def test_bool_before_int_materially_distinct() -> None:
    n_bool = _node(_table_id("b"), attributes={"flag": True})
    n_int = _node(_table_id("i"), attributes={"flag": 1})
    assert n_bool.to_dict()["attributes"]["flag"] is True
    assert n_int.to_dict()["attributes"]["flag"] == 1
    assert n_bool.attributes_canonical != n_int.attributes_canonical


def test_non_finite_floats_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        _node(_table_id(), attributes={"x": math.nan})
    with pytest.raises(ValueError, match="finite"):
        _node(_table_id(), attributes={"x": math.inf})
    with pytest.raises(ValueError, match="finite"):
        _node(_table_id(), attributes={"x": -math.inf})


def test_mutation_after_construction_does_not_affect_node() -> None:
    nested: dict[str, Any] = {"inner": {"k": 1}, "list": [1, 2]}
    attrs: dict[str, Any] = {"nested": nested}
    node = _node(_table_id(), attributes=attrs)
    attrs["nested"] = {"k": 99}
    nested["list"].append(3)
    nested["inner"]["k"] = 99
    assert node.to_dict()["attributes"] == {"nested": {"inner": {"k": 1}, "list": [1, 2]}}


def test_nested_dict_key_order_not_material() -> None:
    a = _node(_table_id(), attributes={"b": 1, "a": {"z": 1, "y": 2}})
    b = _node(_table_id(), attributes={"a": {"y": 2, "z": 1}, "b": 1})
    assert a.attributes_canonical == b.attributes_canonical
    assert a.to_dict()["attributes"] == b.to_dict()["attributes"]


def test_nested_list_order_is_material() -> None:
    a = _node(_table_id("a"), attributes={"cols": ["x", "y"]})
    b = _node(_table_id("b"), attributes={"cols": ["y", "x"]})
    assert a.attributes_canonical != b.attributes_canonical


def test_unsupported_attribute_type_rejected() -> None:
    with pytest.raises(TypeError, match="unsupported"):
        _node(_table_id(), attributes={"bad": {1, 2}})  # type: ignore[dict-item]


def test_independently_allocated_equivalent_nested_json() -> None:
    a = _node(
        _table_id(),
        attributes={"meta": {"owner": "data", "tags": ["a", "b"]}},
        provenance=(_prov("postgresql", "pg://orders"),),
    )
    b = _node(
        GraphNodeIdentity(NS, NODE_KIND_TABLE, "orders"),
        name="orders",
        attributes={"meta": {"tags": ["a", "b"], "owner": "data"}},
        provenance=(_prov("postgresql", "pg://orders"),),
    )
    assert a.to_dict() == b.to_dict()


def test_node_name_stripped_and_required() -> None:
    node = _node(_table_id(), name="  Orders  ")
    assert node.name == "Orders"
    with pytest.raises(ValueError, match="name"):
        GraphNode(identity=_table_id(), name="   ")


def test_node_duplicate_exact_dedup_in_graph() -> None:
    identity = _table_id()
    graph = GovernanceGraph.from_parts(
        [
            _node(identity, provenance=(_prov("postgresql", "pg://orders"),)),
            _node(identity, provenance=(_prov("postgresql", "pg://orders"),)),
        ]
    )
    assert len(graph.nodes) == 1


def test_node_provenance_union_same_material_payload() -> None:
    identity = _table_id()
    attrs = {"grain": "daily"}
    n1 = _node(
        identity,
        description="Orders",
        attributes=attrs,
        provenance=(_prov("postgresql", "pg://orders"),),
    )
    n2 = _node(
        identity,
        description="Orders",
        attributes={"grain": "daily"},
        provenance=(_prov("dbt", "model.orders", "1", "declared"),),
    )
    graph = GovernanceGraph.from_parts([n1, n2])
    assert len(graph.nodes) == 1
    providers = [p.provider_type for p in graph.nodes[0].provenance]
    assert providers == ["dbt", "postgresql"]


def test_node_provenance_union_insertion_order_independent() -> None:
    identity = _table_id()
    attrs = {"grain": "daily"}
    n_pg = _node(
        identity,
        description="Orders",
        attributes=attrs,
        provenance=(_prov("postgresql", "pg://orders"),),
    )
    n_dbt = _node(
        identity,
        description="Orders",
        attributes=dict(attrs),
        provenance=(_prov("dbt", "model.orders", "1", "declared"),),
    )
    g1 = GovernanceGraph.from_parts([n_pg, n_dbt])
    g2 = GovernanceGraph.from_parts([n_dbt, n_pg])
    assert g1.to_dict() == g2.to_dict()


def test_node_conflict_on_different_attributes_rejected() -> None:
    identity = _table_id()
    with pytest.raises(ValueError, match="conflicting GraphNode"):
        GovernanceGraph.from_parts(
            [
                _node(identity, attributes={"a": 1}),
                _node(identity, attributes={"a": 2}),
            ]
        )


def test_node_conflict_on_different_name_rejected() -> None:
    identity = _table_id()
    with pytest.raises(ValueError, match="conflicting GraphNode"):
        GovernanceGraph.from_parts(
            [
                _node(identity, name="Orders"),
                _node(identity, name="Order"),
            ]
        )


def test_parent_missing_in_graph_rejected() -> None:
    column = _node(_column_id())
    with pytest.raises(ValueError, match="parent identity not present"):
        GovernanceGraph.from_parts([column])


# --- D: Edges ---


def test_edge_source_target_validation() -> None:
    table = _node(_table_id())
    contract_id = GraphNodeIdentity(NS, NODE_KIND_CONTRACT, "orders_contract")
    contract = _node(contract_id, name="orders_contract")
    with pytest.raises(ValueError, match="edge source not present"):
        GovernanceGraph.from_parts(
            [table],
            [
                GraphEdge(
                    source=contract_id,
                    target=_table_id(),
                    kind=EDGE_KIND_GOVERNS,
                )
            ],
        )
    graph = GovernanceGraph.from_parts(
        [table, contract],
        [
            GraphEdge(
                source=contract_id,
                target=_table_id(),
                kind=EDGE_KIND_GOVERNS,
                provenance=(_prov("odcs", "contract://orders", "1", "declared"),),
            )
        ],
    )
    assert len(graph.edges) == 1


def test_edge_identity_independent_of_attrs_and_provenance() -> None:
    a = _table_id("a")
    b = _table_id("b")
    e1 = GraphEdge(
        source=a,
        target=b,
        kind=EDGE_KIND_DEPENDS_ON,
        attributes={"w": 1},
        provenance=(_prov("dbt", "ref.a"),),
    )
    e2 = GraphEdge(
        source=a,
        target=b,
        kind=EDGE_KIND_DEPENDS_ON,
        attributes={"w": 1},
        provenance=(_prov("openlineage", "run://x"),),
    )
    assert e1.logical_identity() == e2.logical_identity()


def test_edge_provenance_merge_and_attr_conflict() -> None:
    a_id = _table_id("a")
    b_id = _table_id("b")
    nodes = [_node(a_id), _node(b_id)]
    e1 = GraphEdge(
        source=a_id,
        target=b_id,
        kind=EDGE_KIND_DEPENDS_ON,
        attributes={"confidence": "high"},
        provenance=(_prov("dbt", "ref.a"),),
    )
    e2 = GraphEdge(
        source=a_id,
        target=b_id,
        kind=EDGE_KIND_DEPENDS_ON,
        attributes={"confidence": "high"},
        provenance=(_prov("openlineage", "ol://edge"),),
    )
    g1 = GovernanceGraph.from_parts(nodes, [e1, e2])
    g2 = GovernanceGraph.from_parts(list(reversed(nodes)), [e2, e1])
    assert g1.to_dict() == g2.to_dict()
    assert [p.provider_type for p in g1.edges[0].provenance] == ["dbt", "openlineage"]

    with pytest.raises(ValueError, match="conflicting GraphEdge"):
        GovernanceGraph.from_parts(
            nodes,
            [
                e1,
                GraphEdge(
                    source=a_id,
                    target=b_id,
                    kind=EDGE_KIND_DEPENDS_ON,
                    attributes={"confidence": "low"},
                ),
            ],
        )


def test_edge_exact_duplicate_dedup() -> None:
    a_id = _table_id("a")
    b_id = _table_id("b")
    edge = GraphEdge(source=a_id, target=b_id, kind=EDGE_KIND_CONTAINS)
    graph = GovernanceGraph.from_parts([_node(a_id), _node(b_id)], [edge, edge])
    assert len(graph.edges) == 1


# --- E: GovernanceGraph ---


def test_graph_permutation_independence() -> None:
    ds = GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, "warehouse")
    table = _table_id()
    column = _column_id(parent=table)
    contract = GraphNodeIdentity(NS, NODE_KIND_CONTRACT, "orders_v1")
    nodes = [
        _node(ds, name="warehouse"),
        _node(table, name="orders", provenance=(_prov("postgresql", "pg://orders"),)),
        _node(column, name="customer_id"),
        _node(
            contract, name="orders_v1", provenance=(_prov("odcs", "c://orders", "1", "declared"),)
        ),
    ]
    edges = [
        GraphEdge(source=ds, target=table, kind=EDGE_KIND_CONTAINS),
        GraphEdge(source=table, target=column, kind=EDGE_KIND_CONTAINS),
        GraphEdge(source=contract, target=table, kind=EDGE_KIND_GOVERNS),
    ]
    g1 = GovernanceGraph.from_parts(nodes, edges)
    g2 = GovernanceGraph.from_parts(list(reversed(nodes)), list(reversed(edges)))
    assert g1.to_dict() == g2.to_dict()
    assert len(g1.nodes) == 4
    assert len(g1.edges) == 3


def test_graph_independent_python_allocations_same_dict() -> None:
    table = GraphNodeIdentity("acme.commerce", "table", "orders")
    g1 = GovernanceGraph.from_parts(
        [
            GraphNode(
                identity=table,
                name="orders",
                attributes={"a": {"x": 1, "y": [1, 2]}},
                provenance=(ProvenanceRecord("postgresql", "pg://orders"),),
            )
        ]
    )
    g2 = GovernanceGraph.from_parts(
        [
            GraphNode(
                identity=GraphNodeIdentity("acme.commerce", "table", "orders"),
                name="orders",
                attributes={"a": {"y": [1, 2], "x": 1}},
                provenance=(ProvenanceRecord("postgresql", "pg://orders"),),
            )
        ]
    )
    assert g1.to_dict() == g2.to_dict()


def test_canonical_dict_has_sorted_nodes_and_edges() -> None:
    z_id = GraphNodeIdentity(NS, NODE_KIND_TABLE, "zeta")
    a_id = GraphNodeIdentity(NS, NODE_KIND_TABLE, "alpha")
    m_id = GraphNodeIdentity(NS, NODE_KIND_TABLE, "mu")
    edges = [
        GraphEdge(source=z_id, target=m_id, kind=EDGE_KIND_DEPENDS_ON),
        GraphEdge(source=a_id, target=z_id, kind=EDGE_KIND_DEPENDS_ON),
        GraphEdge(source=a_id, target=m_id, kind=EDGE_KIND_CONTAINS),
    ]
    graph = GovernanceGraph.from_parts([_node(z_id), _node(a_id), _node(m_id)], edges)
    reversed_graph = GovernanceGraph.from_parts(
        [_node(m_id), _node(z_id), _node(a_id)],
        list(reversed(edges)),
    )
    node_ids = [node["identity"]["logical_id"] for node in graph.to_dict()["nodes"]]
    assert node_ids == ["alpha", "mu", "zeta"]
    edge_keys = [
        (edge["source"]["logical_id"], edge["kind"], edge["target"]["logical_id"])
        for edge in graph.to_dict()["edges"]
    ]
    assert len(edge_keys) == 3
    assert edge_keys == [
        (edge["source"]["logical_id"], edge["kind"], edge["target"]["logical_id"])
        for edge in reversed_graph.to_dict()["edges"]
    ]
    assert edge_keys == sorted(edge_keys)


def test_direct_constructor_reversed_nodes_same_dict() -> None:
    z = _node(GraphNodeIdentity(NS, NODE_KIND_TABLE, "zeta"))
    a = _node(GraphNodeIdentity(NS, NODE_KIND_TABLE, "alpha"))
    g1 = GovernanceGraph(nodes=(z, a))
    g2 = GovernanceGraph(nodes=(a, z))
    assert g1.to_dict() == g2.to_dict()
    assert g1.to_dict() == GovernanceGraph.from_parts([z, a]).to_dict()


def test_direct_constructor_reversed_edges_same_dict() -> None:
    a_id = _table_id("a")
    b_id = _table_id("b")
    c_id = _table_id("c")
    nodes = (_node(a_id), _node(b_id), _node(c_id))
    e1 = GraphEdge(source=a_id, target=b_id, kind=EDGE_KIND_DEPENDS_ON)
    e2 = GraphEdge(source=a_id, target=c_id, kind=EDGE_KIND_CONTAINS)
    g1 = GovernanceGraph(nodes=nodes, edges=(e1, e2))
    g2 = GovernanceGraph(nodes=nodes, edges=(e2, e1))
    assert g1.to_dict() == g2.to_dict()


def test_direct_constructor_dangling_edge_rejected() -> None:
    a_id = _table_id("a")
    missing = _table_id("missing")
    with pytest.raises(ValueError, match="edge target not present"):
        GovernanceGraph(
            nodes=(_node(a_id),),
            edges=(GraphEdge(source=a_id, target=missing, kind=EDGE_KIND_DEPENDS_ON),),
        )


def test_direct_constructor_compatible_node_provenance_union() -> None:
    identity = _table_id()
    g = GovernanceGraph(
        nodes=(
            _node(identity, provenance=(_prov("postgresql", "pg://orders"),)),
            _node(identity, provenance=(_prov("dbt", "model.orders", "1", "declared"),)),
        )
    )
    assert len(g.nodes) == 1
    assert [p.provider_type for p in g.nodes[0].provenance] == ["dbt", "postgresql"]


def test_direct_constructor_conflicting_node_payload_rejected() -> None:
    identity = _table_id()
    with pytest.raises(ValueError, match="conflicting GraphNode"):
        GovernanceGraph(
            nodes=(
                _node(identity, attributes={"a": 1}),
                _node(identity, attributes={"a": 2}),
            )
        )


def test_direct_constructor_compatible_edge_provenance_union() -> None:
    a_id = _table_id("a")
    b_id = _table_id("b")
    g = GovernanceGraph(
        nodes=(_node(a_id), _node(b_id)),
        edges=(
            GraphEdge(
                source=a_id,
                target=b_id,
                kind=EDGE_KIND_DEPENDS_ON,
                provenance=(_prov("dbt", "ref.a"),),
            ),
            GraphEdge(
                source=a_id,
                target=b_id,
                kind=EDGE_KIND_DEPENDS_ON,
                provenance=(_prov("openlineage", "ol://edge"),),
            ),
        ),
    )
    assert len(g.edges) == 1
    assert [p.provider_type for p in g.edges[0].provenance] == ["dbt", "openlineage"]


def test_direct_constructor_conflicting_edge_attrs_rejected() -> None:
    a_id = _table_id("a")
    b_id = _table_id("b")
    with pytest.raises(ValueError, match="conflicting GraphEdge"):
        GovernanceGraph(
            nodes=(_node(a_id), _node(b_id)),
            edges=(
                GraphEdge(
                    source=a_id,
                    target=b_id,
                    kind=EDGE_KIND_DEPENDS_ON,
                    attributes={"w": 1},
                ),
                GraphEdge(
                    source=a_id,
                    target=b_id,
                    kind=EDGE_KIND_DEPENDS_ON,
                    attributes={"w": 2},
                ),
            ),
        )


def test_invalid_observation_mode_rejected() -> None:
    with pytest.raises(ValueError, match="observation_mode"):
        ProvenanceRecord("postgresql", "pg://x", observation_mode="guessed")


def test_description_non_str_rejected() -> None:
    with pytest.raises(TypeError, match="description"):
        GraphNode(identity=_table_id(), name="orders", description=123)  # type: ignore[arg-type]


def test_edge_kind_empty_rejected() -> None:
    with pytest.raises(ValueError, match="kind"):
        GraphEdge(source=_table_id("a"), target=_table_id("b"), kind="  ")


def test_str_bytes_bytearray_not_treated_as_json_arrays() -> None:
    node = _node(_table_id(), attributes={"s": "ab", "b": "x"})
    assert node.to_dict()["attributes"]["s"] == "ab"
    with pytest.raises(TypeError, match="unsupported"):
        _node(_table_id(), attributes={"raw": b"abc"})
    with pytest.raises(TypeError, match="unsupported"):
        _node(_table_id(), attributes={"raw": bytearray(b"abc")})


def test_graph_rejects_non_node_and_non_edge_objects() -> None:
    with pytest.raises(TypeError, match="GraphNode"):
        GovernanceGraph(nodes=("not-a-node",))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="GraphEdge"):
        GovernanceGraph(
            nodes=(_node(_table_id()),),
            edges=("not-an-edge",),  # type: ignore[arg-type]
        )


# --- F: Content identity ---


def test_content_identity_identical_semantic_graphs() -> None:
    table = _table_id()
    g1 = GovernanceGraph.from_parts(
        [
            _node(
                table,
                attributes={"meta": {"a": 1, "b": [1, 2]}},
                provenance=(_prov("postgresql", "pg://orders"), _prov("dbt", "m.orders", "1")),
            )
        ]
    )
    g2 = GovernanceGraph.from_parts(
        [
            _node(
                GraphNodeIdentity(NS, NODE_KIND_TABLE, "orders"),
                name="orders",
                attributes={"meta": {"b": [1, 2], "a": 1}},
                provenance=(
                    _prov("dbt", "m.orders", "1"),
                    _prov("postgresql", "pg://orders"),
                ),
            )
        ]
    )
    assert g1.content_identity() == g2.content_identity()
    assert "content_identity" not in g1.canonical_dict_without_identity()
    assert g1.to_dict()["content_identity"]["digest"] == g1.content_identity().digest


def test_content_identity_changes_on_material_node_and_edge() -> None:
    a_id = _table_id("a")
    b_id = _table_id("b")
    base = GovernanceGraph.from_parts(
        [_node(a_id), _node(b_id)],
        [GraphEdge(source=a_id, target=b_id, kind=EDGE_KIND_DEPENDS_ON)],
    )
    changed_node = GovernanceGraph.from_parts(
        [_node(a_id, attributes={"x": 1}), _node(b_id)],
        [GraphEdge(source=a_id, target=b_id, kind=EDGE_KIND_DEPENDS_ON)],
    )
    changed_edge = GovernanceGraph.from_parts(
        [_node(a_id), _node(b_id)],
        [
            GraphEdge(
                source=a_id,
                target=b_id,
                kind=EDGE_KIND_DEPENDS_ON,
                attributes={"w": 1},
            )
        ],
    )
    assert base.content_identity() != changed_node.content_identity()
    assert base.content_identity() != changed_edge.content_identity()


def test_content_identity_object_vs_array_and_bool_vs_int() -> None:
    obj = GovernanceGraph.from_parts([_node(_table_id("t"), attributes={"v": {"a": 1}})])
    arr = GovernanceGraph.from_parts([_node(_table_id("t"), attributes={"v": [["a", 1]]})])
    assert obj.content_identity() != arr.content_identity()

    as_bool = GovernanceGraph.from_parts([_node(_table_id("t"), attributes={"flag": True})])
    as_int = GovernanceGraph.from_parts([_node(_table_id("t"), attributes={"flag": 1})])
    assert as_bool.content_identity() != as_int.content_identity()


def test_content_identity_insertion_order_and_mutation_independence() -> None:
    nested: dict[str, Any] = {"k": 1, "list": [1, 2]}
    attrs: dict[str, Any] = {"nested": nested}
    n1 = _node(_table_id(), attributes=attrs, provenance=(_prov("postgresql", "pg://o"),))
    n2 = _node(
        _table_id(),
        attributes={"nested": {"list": [1, 2], "k": 1}},
        provenance=(_prov("dbt", "m.o", "1"),),
    )
    g1 = GovernanceGraph.from_parts([n1, n2])
    g2 = GovernanceGraph.from_parts([n2, n1])
    digest = g1.content_identity()
    assert digest == g2.content_identity()

    attrs["nested"] = {"k": 99}
    nested["list"].append(9)
    assert g1.content_identity() == digest


def test_node_provenance_union_same_digest_regardless_of_order() -> None:
    identity = _table_id()
    n_pg = _node(
        identity,
        attributes={"grain": "daily"},
        provenance=(_prov("postgresql", "pg://orders"),),
    )
    n_dbt = _node(
        identity,
        attributes={"grain": "daily"},
        provenance=(_prov("dbt", "model.orders", "1", "declared"),),
    )
    assert (
        GovernanceGraph.from_parts([n_pg, n_dbt]).content_identity()
        == GovernanceGraph.from_parts([n_dbt, n_pg]).content_identity()
    )


def test_direct_constructor_semantic_equality_same_content_identity() -> None:
    z = _node(GraphNodeIdentity(NS, NODE_KIND_TABLE, "zeta"))
    a = _node(GraphNodeIdentity(NS, NODE_KIND_TABLE, "alpha"))
    g1 = GovernanceGraph(nodes=(z, a))
    g2 = GovernanceGraph(nodes=(a, z))
    g3 = GovernanceGraph.from_parts([z, a])
    assert g1.content_identity() == g2.content_identity() == g3.content_identity()


def test_direct_constructor_edge_permutation_same_content_identity() -> None:
    a_id = _table_id("a")
    b_id = _table_id("b")
    c_id = _table_id("c")
    nodes = (_node(a_id), _node(b_id), _node(c_id))
    e1 = GraphEdge(source=a_id, target=b_id, kind=EDGE_KIND_DEPENDS_ON)
    e2 = GraphEdge(source=a_id, target=c_id, kind=EDGE_KIND_CONTAINS)
    assert (
        GovernanceGraph(nodes=nodes, edges=(e1, e2)).content_identity()
        == GovernanceGraph(nodes=nodes, edges=(e2, e1)).content_identity()
    )
