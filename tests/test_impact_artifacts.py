"""Versioned impact changes/result contracts, policy relevance, and human formatting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from governance.domain import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    EDGE_KIND_GOVERNS,
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    GovernanceGraph,
    GraphEdge,
    GraphNode,
    GraphNodeIdentity,
    analyze_downstream_impact,
)
from governance.domain.models import make_database_id, make_schema_id, make_table_id
from governance.identity import impact_result_identity, policy_identity
from governance.impact import (
    CHANGES_SCHEMA,
    CHANGES_VERSION,
    CODE_INTEGRITY,
    CODE_PARSE,
    CODE_READ,
    CODE_UNSUPPORTED,
    IMPACT_DIAGNOSTIC_SCHEMA,
    RESULT_SCHEMA,
    RESULT_VERSION,
    ImpactChangedNodeError,
    ImpactDiagnosticError,
    ImpactIntegrityError,
    ImpactParseError,
    ImpactReadError,
    ImpactSchemaError,
    UnsupportedImpactVersionError,
    build_impact_result,
    canonical_impact_json,
    format_human_value,
    format_impact_result_human,
    identity_from_dict,
    impact_diagnostics_failure,
    load_changes_schema,
    load_impact_changes,
    load_impact_result,
    load_result_schema,
    match_affected_policies,
    project_physical_selector_target,
    selector_matches,
    validate_changes_document,
    validate_result_document,
    verify_impact_result_integrity,
    write_impact_result,
)
from governance.impact.policy import ProjectedObject
from governance.integrations.dbt import load_dbt_graph
from governance.integrations.odcs import load_odcs_graph
from governance.integrations.openlineage import load_openlineage_graph
from governance.policy.models import (
    NormalizedPolicy,
    NormalizedPolicySet,
    PolicySelector,
)

NS = "analytics"


def _id(
    kind: str,
    logical_id: str,
    parent: GraphNodeIdentity | None = None,
    *,
    namespace: str = NS,
) -> GraphNodeIdentity:
    return GraphNodeIdentity(namespace, kind, logical_id, parent=parent)


def _node(identity: GraphNodeIdentity, *, name: str | None = None) -> GraphNode:
    return GraphNode(
        identity=identity,
        name=name if name is not None else identity.logical_id,
        description=None,
        attributes={},
        provenance=(),
    )


def _graph(
    identities: list[GraphNodeIdentity],
    edges: list[GraphEdge],
    *,
    names: dict[GraphNodeIdentity, str] | None = None,
) -> GovernanceGraph:
    name_map = names or {}
    return GovernanceGraph.from_parts(
        [_node(identity, name=name_map.get(identity)) for identity in identities],
        edges,
    )


def _physical_table(table_name: str = "orders") -> tuple[GraphNodeIdentity, ...]:
    ds = _id(NODE_KIND_DATA_SOURCE, "db")
    schema = _id(NODE_KIND_DATASET, "public", ds)
    table = _id(NODE_KIND_TABLE, table_name, schema)
    return ds, schema, table


def _changes_doc(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "changes_schema": CHANGES_SCHEMA,
        "changes_version": CHANGES_VERSION,
        "changed_nodes": nodes,
    }


def _write_changes(path: Path, nodes: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps(_changes_doc(nodes)), encoding="utf-8")
    return path


# --- A. Changes input contract ---


def test_valid_changes_loads_parent_aware_identity(tmp_path: Path) -> None:
    ds, schema, table = _physical_table()
    path = _write_changes(tmp_path / "changes.json", [table.to_dict()])
    loaded = load_impact_changes(path, expected_namespace=NS)
    assert loaded == (table,)
    assert loaded[0].parent == schema
    assert loaded[0].parent is not None
    assert loaded[0].parent.parent == ds


def test_column_nested_chain_roundtrip(tmp_path: Path) -> None:
    ds, schema, table = _physical_table()
    column = _id(NODE_KIND_COLUMN, "customer_id", table)
    path = _write_changes(tmp_path / "changes.json", [column.to_dict()])
    loaded = load_impact_changes(path, expected_namespace=NS)
    assert loaded[0] == column
    assert loaded[0].to_dict() == column.to_dict()


def test_changes_min_one_node_required() -> None:
    with pytest.raises(ImpactSchemaError):
        validate_changes_document(_changes_doc([]))


def test_changes_unknown_field_rejected() -> None:
    doc = _changes_doc([_id(NODE_KIND_TABLE, "orders").to_dict()])
    doc["extra"] = 1
    with pytest.raises(ImpactSchemaError):
        validate_changes_document(doc)


def test_changes_missing_field_rejected() -> None:
    doc = {
        "changes_schema": CHANGES_SCHEMA,
        "changes_version": CHANGES_VERSION,
    }
    with pytest.raises(ImpactSchemaError):
        validate_changes_document(doc)


def test_changes_unsupported_version() -> None:
    doc = _changes_doc([_id(NODE_KIND_TABLE, "orders").to_dict()])
    doc["changes_version"] = "2"
    with pytest.raises(UnsupportedImpactVersionError) as exc:
        validate_changes_document(doc)
    assert exc.value.errors[0].code == CODE_UNSUPPORTED


def test_changes_wrong_schema_name_rejected() -> None:
    doc = _changes_doc([_id(NODE_KIND_TABLE, "orders").to_dict()])
    doc["changes_schema"] = "other"
    with pytest.raises((ImpactSchemaError, UnsupportedImpactVersionError)):
        validate_changes_document(doc)


def test_changes_malformed_json_safe(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ImpactParseError) as exc:
        load_impact_changes(path, expected_namespace=NS)
    assert exc.value.errors[0].code == CODE_PARSE


def test_changes_read_failure_safe(tmp_path: Path) -> None:
    with pytest.raises(ImpactReadError) as exc:
        load_impact_changes(tmp_path / "missing.json", expected_namespace=NS)
    assert exc.value.errors[0].code == CODE_READ


def test_changes_invalid_kind_rejected() -> None:
    node = _id(NODE_KIND_TABLE, "orders").to_dict()
    node["kind"] = "view"
    with pytest.raises(ImpactSchemaError):
        validate_changes_document(_changes_doc([node]))


def test_column_without_parent_rejected_by_domain(tmp_path: Path) -> None:
    node = {
        "namespace": NS,
        "kind": "column",
        "logical_id": "id",
        "parent": None,
    }
    path = _write_changes(tmp_path / "changes.json", [node])
    with pytest.raises(ImpactChangedNodeError):
        load_impact_changes(path, expected_namespace=NS)


def test_duplicate_changed_nodes_accepted(tmp_path: Path) -> None:
    table = _id(NODE_KIND_TABLE, "orders")
    path = _write_changes(tmp_path / "changes.json", [table.to_dict(), table.to_dict()])
    loaded = load_impact_changes(path, expected_namespace=NS)
    assert loaded == (table, table)
    graph = _graph([table], [])
    result = analyze_downstream_impact(graph, loaded)
    assert result.changed_nodes == (table,)


def test_namespace_mismatch_raises(tmp_path: Path) -> None:
    table = _id(NODE_KIND_TABLE, "orders", namespace="other")
    path = _write_changes(tmp_path / "changes.json", [table.to_dict()])
    with pytest.raises(ImpactChangedNodeError):
        load_impact_changes(path, expected_namespace=NS)


# --- B. Result contract ---


def _clear_result() -> tuple[GovernanceGraph, dict[str, Any]]:
    root = _id(NODE_KIND_TABLE, "isolated")
    graph = _graph([root], [])
    impact = analyze_downstream_impact(graph, [root])
    payload = build_impact_result(graph=graph, impact=impact)
    return graph, payload


def _impacted_result() -> tuple[GovernanceGraph, dict[str, Any]]:
    a = _id(NODE_KIND_TABLE, "a")
    b = _id(NODE_KIND_TABLE, "b")
    graph = _graph([a, b], [GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON)])
    impact = analyze_downstream_impact(graph, [a])
    payload = build_impact_result(graph=graph, impact=impact)
    return graph, payload


def test_build_clear_result() -> None:
    _, payload = _clear_result()
    assert payload["status"] == "clear"
    assert payload["impact_detected"] is False
    assert payload["writes_performed"] == 0
    assert payload["result_schema"] == RESULT_SCHEMA
    assert payload["result_version"] == RESULT_VERSION
    assert payload["policy_identity"] is None
    assert payload["affected_policies"] == []


def test_build_impacted_result() -> None:
    _, payload = _impacted_result()
    assert payload["status"] == "impacted"
    assert payload["impact_detected"] is True
    assert payload["writes_performed"] == 0
    assert len(payload["impact"]["direct_nodes"]) == 1


def test_result_schema_validates() -> None:
    _, payload = _impacted_result()
    validate_result_document(payload)


def test_result_additional_field_rejected() -> None:
    _, payload = _clear_result()
    payload = dict(payload)
    payload["extra"] = True
    with pytest.raises(ImpactSchemaError):
        validate_result_document(payload)


def test_graph_identity_exact() -> None:
    graph, payload = _clear_result()
    assert payload["graph_identity"] == graph.content_identity().to_dict()


def test_policy_identity_with_config() -> None:
    root = _id(NODE_KIND_TABLE, "isolated")
    graph = _graph([root], [])
    impact = analyze_downstream_impact(graph, [root])
    policy_set = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="p1",
                severity="warning",
                rule_type="require_description",
                select=PolicySelector(kind="table"),
            ),
        )
    )
    payload = build_impact_result(graph=graph, impact=impact, policy_set=policy_set)
    assert payload["policy_identity"] == policy_identity(policy_set.to_identity_dict()).to_dict()


def test_content_identity_recomputes_and_tamper_rejected() -> None:
    _, payload = _clear_result()
    verify_impact_result_integrity(payload)
    without = {k: v for k, v in payload.items() if k != "content_identity"}
    assert payload["content_identity"] == impact_result_identity(without).to_dict()
    tampered = dict(payload)
    tampered["status"] = "impacted"
    with pytest.raises(ImpactIntegrityError) as exc:
        verify_impact_result_integrity(tampered)
    assert exc.value.errors[0].code == CODE_INTEGRITY


def test_deterministic_serialization_and_atomic_write(tmp_path: Path) -> None:
    _, payload = _impacted_result()
    text_a = canonical_impact_json(payload)
    text_b = canonical_impact_json(payload)
    assert text_a == text_b
    assert text_a.endswith("\n")
    assert "\n" not in text_a[:-1]
    out = write_impact_result(tmp_path / "result.json", payload)
    assert out.read_text(encoding="utf-8") == text_a
    loaded = load_impact_result(out)
    assert loaded == payload


def test_loader_rejects_unsupported_result_version() -> None:
    _, payload = _clear_result()
    bad = dict(payload)
    bad["result_version"] = "9"
    with pytest.raises(UnsupportedImpactVersionError):
        validate_result_document(bad)


def test_result_excludes_source_paths_and_timestamps() -> None:
    _, payload = _impacted_result()
    blob = canonical_impact_json(payload)
    for needle in ("tmp", "C:\\", "/Users", "timestamp", "run_id", "argv"):
        assert needle not in blob


def test_impact_object_equals_domain_to_dict() -> None:
    a = _id(NODE_KIND_TABLE, "a")
    b = _id(NODE_KIND_TABLE, "b")
    graph = _graph([a, b], [GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON)])
    impact = analyze_downstream_impact(graph, [a])
    payload = build_impact_result(graph=graph, impact=impact)
    assert payload["impact"] == impact.to_dict()


# --- C. Graph composition (package-level helpers via providers) ---


def _odcs_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": "contract-orders",
        "version": "1.0.0",
        "status": "active",
        "name": "Orders Contract",
        "schema": [
            {
                "name": "orders",
                "logicalType": "object",
                "properties": [{"name": "id", "logicalType": "string"}],
            }
        ],
    }
    doc.update(overrides)
    return doc


def _dbt_manifest(model: dict[str, Any] | None = None) -> dict[str, Any]:
    node = model or {
        "unique_id": "model.pkg.orders",
        "resource_type": "model",
        "name": "orders",
        "package_name": "pkg",
        "database": "db",
        "schema": "public",
        "alias": "orders",
        "fqn": ["pkg", "orders"],
        "config": {"materialized": "table"},
        "columns": {"id": {"name": "id", "data_type": "varchar"}},
        "tags": [],
        "meta": {},
        "description": "",
    }
    return {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json",
            "dbt_version": "1.10.0",
            "generated_at": "2024-01-01T00:00:00.000000Z",
            "invocation_id": "inv",
        },
        "nodes": {node["unique_id"]: node},
        "sources": {},
        "parent_map": {},
        "disabled": {},
    }


def _ol_event() -> dict[str, Any]:
    return {
        "eventType": "COMPLETE",
        "eventTime": "2024-01-01T00:00:00.000000Z",
        "producer": "https://example.com",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "run": {"runId": "00000000-0000-4000-8000-000000000001"},
        "job": {"namespace": "ns", "name": "job"},
        "inputs": [],
        "outputs": [
            {
                "namespace": "postgres://db",
                "name": "public.orders",
                "facets": {
                    "schema": {
                        "_producer": "https://example.com",
                        "_schemaURL": (
                            "https://openlineage.io/spec/facets/1-0-0/SchemaDatasetFacet.json"
                        ),
                        "fields": [{"name": "id", "type": "string"}],
                    }
                },
            }
        ],
    }


def test_compose_odcs_dbt_openlineage_and_permutation(tmp_path: Path) -> None:
    odcs_path = tmp_path / "a.odcs.yaml"
    odcs_path.write_text(yaml.safe_dump(_odcs_doc()), encoding="utf-8")
    dbt_path = tmp_path / "manifest.json"
    dbt_path.write_text(json.dumps(_dbt_manifest()), encoding="utf-8")
    ol_path = tmp_path / "ol.json"
    ol_path.write_text(json.dumps([_ol_event()]), encoding="utf-8")

    def compose(specs: list[tuple[str, Path]]) -> GovernanceGraph:
        graphs = []
        for kind, path in sorted(specs, key=lambda item: (item[0], str(item[1]))):
            if kind == "dbt":
                graphs.append(load_dbt_graph(path, namespace=NS))
            elif kind == "odcs":
                graphs.append(load_odcs_graph(path, namespace=NS))
            else:
                graphs.append(load_openlineage_graph(path, namespace=NS))
        return GovernanceGraph.from_parts(
            [n for g in graphs for n in g.nodes],
            [e for g in graphs for e in g.edges],
        )

    specs = [("odcs", odcs_path), ("dbt", dbt_path), ("openlineage", ol_path)]
    g1 = compose(specs)
    g2 = compose(list(reversed(specs)))
    assert g1.to_dict() == g2.to_dict()
    assert g1.content_identity() == g2.content_identity()


def test_repeated_same_source_no_logical_duplication(tmp_path: Path) -> None:
    dbt_path = tmp_path / "manifest.json"
    dbt_path.write_text(json.dumps(_dbt_manifest()), encoding="utf-8")
    g_once = load_dbt_graph(dbt_path, namespace=NS)
    composed = GovernanceGraph.from_parts(
        list(g_once.nodes) + list(g_once.nodes),
        list(g_once.edges) + list(g_once.edges),
    )
    assert composed.to_dict() == g_once.to_dict()


def test_graph_conflict_deterministic() -> None:
    identity = _id(NODE_KIND_TABLE, "orders")
    a = GraphNode(identity=identity, name="orders", description="a", attributes={}, provenance=())
    b = GraphNode(identity=identity, name="orders", description="b", attributes={}, provenance=())
    with pytest.raises(ValueError, match="conflicting GraphNode"):
        GovernanceGraph.from_parts([a, b], [])


def test_dbt_default_database_propagated(tmp_path: Path) -> None:
    model = _dbt_manifest()["nodes"]["model.pkg.orders"]
    model["database"] = None
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_dbt_manifest(model)), encoding="utf-8")
    graph = load_dbt_graph(path, namespace=NS, default_database="fallback_db")
    ds_ids = [
        n.identity.logical_id for n in graph.nodes if n.identity.kind == NODE_KIND_DATA_SOURCE
    ]
    assert "fallback_db" in ds_ids


# --- D. Impact semantics through artifacts ---


def test_isolated_root_clear() -> None:
    _, payload = _clear_result()
    assert payload["status"] == "clear"
    assert payload["impact"]["direct_nodes"] == []
    assert payload["impact"]["transitive_nodes"] == []


def test_direct_and_transitive_impact() -> None:
    a = _id(NODE_KIND_TABLE, "a")
    b = _id(NODE_KIND_TABLE, "b")
    c = _id(NODE_KIND_TABLE, "c")
    graph = _graph(
        [a, b, c],
        [
            GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON),
            GraphEdge(source=c, target=b, kind=EDGE_KIND_DEPENDS_ON),
        ],
    )
    impact = analyze_downstream_impact(graph, [a])
    payload = build_impact_result(graph=graph, impact=impact)
    assert payload["status"] == "impacted"
    assert [n["logical_id"] for n in payload["impact"]["direct_nodes"]] == ["b"]
    assert [n["logical_id"] for n in payload["impact"]["transitive_nodes"]] == ["c"]


def test_contract_governs_and_cycle_artifact() -> None:
    contract = _id(NODE_KIND_CONTRACT, "c1")
    table = _id(NODE_KIND_TABLE, "t1")
    other = _id(NODE_KIND_TABLE, "t2")
    graph = _graph(
        [contract, table, other],
        [
            GraphEdge(source=contract, target=table, kind=EDGE_KIND_GOVERNS),
            GraphEdge(source=other, target=table, kind=EDGE_KIND_DEPENDS_ON),
            GraphEdge(source=table, target=other, kind=EDGE_KIND_DEPENDS_ON),
        ],
    )
    impact = analyze_downstream_impact(graph, [contract])
    payload = build_impact_result(graph=graph, impact=impact)
    assert payload["status"] == "impacted"
    assert payload["impact"]["associated_contracts"]


def test_source_and_root_ordering_byte_identical() -> None:
    a = _id(NODE_KIND_TABLE, "a")
    b = _id(NODE_KIND_TABLE, "b")
    graph = _graph([a, b], [GraphEdge(source=b, target=a, kind=EDGE_KIND_DEPENDS_ON)])
    p1 = build_impact_result(graph=graph, impact=analyze_downstream_impact(graph, [a, a]))
    p2 = build_impact_result(graph=graph, impact=analyze_downstream_impact(graph, [a]))
    assert canonical_impact_json(p1) == canonical_impact_json(p2)


# --- E. Affected policies / selector contract ---


def test_no_config_empty_policies() -> None:
    _, payload = _clear_result()
    assert payload["policy_identity"] is None
    assert payload["affected_policies"] == []


def test_selector_kind_only_match() -> None:
    ds, schema, table = _physical_table()
    projected = project_physical_selector_target(table)
    assert projected is not None
    assert selector_matches(PolicySelector(kind="table"), projected)


def test_selector_exact_object_id_match_and_mismatch() -> None:
    ds, schema, table = _physical_table()
    projected = project_physical_selector_target(table)
    assert projected is not None
    assert selector_matches(
        PolicySelector(kind="table", object_id=projected.object_id),
        projected,
    )
    assert not selector_matches(
        PolicySelector(kind="table", object_id="tbl:analytics/db/public/other"),
        projected,
    )


def test_selector_id_prefix_match_and_mismatch() -> None:
    ds, schema, table = _physical_table()
    projected = project_physical_selector_target(table)
    assert projected is not None
    assert selector_matches(
        PolicySelector(kind="table", id_prefix="tbl:analytics/db/"),
        projected,
    )
    assert not selector_matches(
        PolicySelector(kind="table", id_prefix="tbl:analytics/other/"),
        projected,
    )


def test_selector_object_id_and_id_prefix_both_required() -> None:
    ds, schema, table = _physical_table()
    projected = project_physical_selector_target(table)
    assert projected is not None
    selector = PolicySelector(
        kind="table",
        object_id=projected.object_id,
        id_prefix="tbl:analytics/db/",
    )
    assert selector_matches(selector, projected)
    bad = PolicySelector(
        kind="table",
        object_id=projected.object_id,
        id_prefix="tbl:analytics/other/",
    )
    assert not selector_matches(bad, projected)


def test_selector_kind_mismatch() -> None:
    ds, schema, table = _physical_table()
    projected = project_physical_selector_target(table)
    assert projected is not None
    assert not selector_matches(PolicySelector(kind="column"), projected)


def test_physical_schema_and_database_projection() -> None:
    ds, schema, table = _physical_table()
    ds_proj = project_physical_selector_target(ds)
    schema_proj = project_physical_selector_target(schema)
    assert ds_proj is not None
    assert ds_proj.object_kind == "database"
    assert ds_proj.object_id == make_database_id(NS, "db")
    assert schema_proj is not None
    assert schema_proj.object_kind == "schema"
    assert schema_proj.object_id == make_schema_id(NS, "db", "public")
    assert project_physical_selector_target(table) is not None


def test_generic_odcs_dataset_and_column_not_coerced() -> None:
    dataset = _id(NODE_KIND_DATASET, "orders")
    column = _id(NODE_KIND_COLUMN, "id", dataset)
    assert project_physical_selector_target(dataset) is None
    assert project_physical_selector_target(column) is None


def test_data_source_and_relationship_selectors_not_invented() -> None:
    ds, schema, table = _physical_table()
    policy_set = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="ds-policy",
                severity="warning",
                rule_type="require_description",
                select=PolicySelector(kind="data_source"),
            ),
            NormalizedPolicy(
                id="rel-policy",
                severity="warning",
                rule_type="require_description",
                select=PolicySelector(kind="relationship"),
            ),
            NormalizedPolicy(
                id="table-policy",
                severity="warning",
                rule_type="require_description",
                select=PolicySelector(kind="table"),
            ),
        )
    )
    affected = match_affected_policies(policy_set, (ds, schema, table))
    assert [item.policy_id for item in affected] == ["table-policy"]


def test_multiple_projected_objects_dedup_and_sort() -> None:
    ds, schema, t1 = _physical_table("orders")
    t2 = _id(NODE_KIND_TABLE, "customers", schema)
    policy_set = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="z-policy",
                severity="warning",
                rule_type="require_description",
                select=PolicySelector(kind="table"),
            ),
            NormalizedPolicy(
                id="a-policy",
                severity="warning",
                rule_type="require_description",
                select=PolicySelector(kind="table"),
            ),
        )
    )
    affected = match_affected_policies(policy_set, (t2, t1, t1, schema, ds))
    assert [item.policy_id for item in affected] == ["a-policy", "z-policy"]
    ids = [m.object_id for m in affected[0].matched_objects]
    assert ids == sorted(ids)
    assert len(ids) == 2


def test_unrelated_policy_omitted() -> None:
    ds, schema, table = _physical_table()
    policy_set = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="other",
                severity="warning",
                rule_type="require_description",
                select=PolicySelector(
                    kind="table",
                    object_id=make_table_id(NS, "db", "public", "other"),
                ),
            ),
        )
    )
    assert match_affected_policies(policy_set, (table,)) == ()


def test_error_severity_relevance_only() -> None:
    ds, schema, table = _physical_table()
    policy_set = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="err",
                severity="error",
                rule_type="require_owner",
                select=PolicySelector(kind="table"),
            ),
        )
    )
    graph = _graph(
        [ds, schema, table],
        [
            GraphEdge(source=ds, target=schema, kind=EDGE_KIND_CONTAINS),
            GraphEdge(source=schema, target=table, kind=EDGE_KIND_CONTAINS),
        ],
    )
    impact = analyze_downstream_impact(graph, [table])
    affected = match_affected_policies(policy_set, impact.policy_relevant_nodes)
    payload = build_impact_result(
        graph=graph,
        impact=impact,
        affected_policies=affected,
        policy_set=policy_set,
    )
    assert payload["affected_policies"][0]["severity"] == "error"
    assert payload["status"] == "clear"


# --- Human line-safety / diagnostics ---


def test_format_human_value_escapes_controls() -> None:
    raw = "a\nb\rc\td\x01e"
    escaped = format_human_value(raw)
    assert "\n" not in escaped
    assert "\r" not in escaped
    assert "\t" not in escaped
    assert "\\n" in escaped
    assert "\\r" in escaped
    assert "\\t" in escaped
    assert "\\x01" in escaped
    assert format_human_value("path\\name") == "path\\\\name"


def test_format_human_value_escapes_unicode_separators_and_surrogates() -> None:
    raw = "a\u0085b\u2028c\u2029d\u009be\ud800f"
    escaped = format_human_value(raw)
    assert "\n" not in escaped
    assert "\r" not in escaped
    assert "\u0085" not in escaped
    assert "\u2028" not in escaped
    assert "\u2029" not in escaped
    assert "\u009b" not in escaped
    assert "\ud800" not in escaped
    assert escaped == "a\\x85b\\u2028c\\u2029d\\x9be\\ud800f"
    # Must remain encodable and single-line.
    escaped.encode("utf-8")
    assert len(escaped.splitlines()) == 1


def test_human_output_one_record_per_line_with_control_chars() -> None:
    nasty = "ord\ners"
    identity = _id(NODE_KIND_TABLE, nasty)
    downstream = _id(NODE_KIND_TABLE, "down\x01stream")
    graph = _graph(
        [identity, downstream],
        [GraphEdge(source=downstream, target=identity, kind=EDGE_KIND_DEPENDS_ON)],
        names={identity: "name\nwith\rbreaks", downstream: "d\tname"},
    )
    impact = analyze_downstream_impact(graph, [identity])
    payload = build_impact_result(graph=graph, impact=impact)
    # Machine artifact keeps raw values.
    assert payload["impact"]["changed_nodes"][0]["logical_id"] == nasty
    human = format_impact_result_human(
        payload,
        graph=graph,
        output_path="out\nput.json",
    )
    lines = human.splitlines()
    assert human.endswith("\n")
    assert all("\r" not in line for line in lines)
    assert any(line.startswith("DIRECT ") for line in lines)
    assert any("artifact_written=" in line and "\\n" in line for line in lines)
    assert sum(1 for line in lines if line.startswith("PATH ")) == 1


def test_human_output_one_record_per_line_with_unicode_separators() -> None:
    identity = _id(NODE_KIND_TABLE, "ord\u2028ers")
    downstream = _id(NODE_KIND_TABLE, "down\u0085stream")
    graph = _graph(
        [identity, downstream],
        [GraphEdge(source=downstream, target=identity, kind=EDGE_KIND_DEPENDS_ON)],
        names={identity: "name\u2029x", downstream: "d\u009bname"},
    )
    impact = analyze_downstream_impact(graph, [identity])
    payload = build_impact_result(graph=graph, impact=impact)
    assert payload["impact"]["changed_nodes"][0]["logical_id"] == "ord\u2028ers"
    human = format_impact_result_human(
        payload,
        graph=graph,
        output_path="out\u2028put.json",
    )
    physical_lines = human.split("\n")
    assert human.endswith("\n")
    assert all("\u2028" not in line for line in physical_lines)
    assert all("\u2029" not in line for line in physical_lines)
    assert all("\u0085" not in line for line in physical_lines)
    assert any("\\u2028" in line for line in physical_lines)
    assert sum(1 for line in physical_lines if line.startswith("DIRECT ")) == 1
    assert sum(1 for line in physical_lines if line.startswith("PATH ")) == 1


def test_changes_invalid_utf8_is_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "bad-changes.json"
    path.write_bytes(b"\xff\xfe{")
    with pytest.raises(ImpactParseError) as exc:
        load_impact_changes(path, expected_namespace=NS)
    assert exc.value.errors[0].code == CODE_PARSE
    assert "UTF-8" in exc.value.errors[0].message


def test_result_invalid_utf8_is_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "bad-result.json"
    path.write_bytes(b"\xff\xfe{")
    with pytest.raises(ImpactParseError) as exc:
        load_impact_result(path)
    assert exc.value.errors[0].code == CODE_PARSE
    assert "UTF-8" in exc.value.errors[0].message


def test_diagnostics_deterministic_ordering() -> None:
    errors = [
        ImpactDiagnosticError(code="schema_error", message="b", path="/z"),
        ImpactDiagnosticError(code="schema_error", message="a", path="/a"),
        ImpactDiagnosticError(
            code="source_error",
            message="x",
            path="/a",
            source_kind="dbt",
        ),
        ImpactDiagnosticError(
            code="source_error",
            message="x",
            path="/a",
            source_kind="odcs",
        ),
    ]
    payload = impact_diagnostics_failure(errors)
    assert payload["diagnostic_schema"] == IMPACT_DIAGNOSTIC_SCHEMA
    assert payload["ok"] is False
    codes_paths = [
        (e["path"], e["code"], e["message"], e.get("source_kind", "")) for e in payload["errors"]
    ]
    assert codes_paths == sorted(codes_paths)


def test_packaged_schemas_loadable() -> None:
    changes = load_changes_schema()
    result = load_result_schema()
    assert changes["properties"]["changes_schema"]["const"] == CHANGES_SCHEMA
    assert result["properties"]["result_schema"]["const"] == RESULT_SCHEMA


def test_identity_from_dict_roundtrip() -> None:
    ds, schema, table = _physical_table()
    column = _id(NODE_KIND_COLUMN, "id", table)
    assert identity_from_dict(column.to_dict()) == column


def test_broad_table_selector_matches_physical_table() -> None:
    ds, schema, table = _physical_table()
    policy_set = NormalizedPolicySet(
        policies=(
            NormalizedPolicy(
                id="tables-require-description",
                severity="warning",
                rule_type="require_description",
                select=PolicySelector(kind="table"),
            ),
        )
    )
    affected = match_affected_policies(policy_set, (table,))
    assert len(affected) == 1
    assert affected[0].matched_objects[0].object_id == make_table_id(NS, "db", "public", "orders")


def test_projected_object_type() -> None:
    ds, _, _ = _physical_table()
    projected = project_physical_selector_target(ds)
    assert isinstance(projected, ProjectedObject)
