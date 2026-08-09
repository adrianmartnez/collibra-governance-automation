"""Tests for dbt Manifest v12 subset loading, validation, and mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from governance import __version__
from governance.domain.graph import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    NODE_KIND_COLUMN,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    NODE_KIND_TRANSFORMATION,
)
from governance.identity.canonicalize import canonical_json_bytes
from governance.integrations.dbt import (
    CODE_MAPPING,
    CODE_PARSE,
    CODE_READ,
    CODE_UNSUPPORTED_MANIFEST_VERSION,
    CODE_VALIDATION,
    DbtDiagnostic,
    DbtMappingError,
    DbtParseError,
    DbtReadError,
    DbtUnsupportedManifestVersionError,
    DbtValidationError,
    load_dbt_graph,
    load_dbt_manifest,
    map_dbt_manifest,
    validate_dbt_manifest,
)
from governance.integrations.dbt.validate import SUPPORTED_MANIFEST_SCHEMA_URI

NS = "acme.analytics"
V12 = SUPPORTED_MANIFEST_SCHEMA_URI


def _minimal_v12_manifest(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "metadata": {
            "dbt_schema_version": V12,
            "dbt_version": "1.10.0",
            "generated_at": "2024-01-01T00:00:00.000000Z",
            "invocation_id": "inv-volatile",
        },
        "nodes": {},
        "sources": {},
        "parent_map": {},
        "disabled": {},
    }
    doc.update(overrides)
    return doc


def _model(
    unique_id: str = "model.pkg.orders",
    *,
    name: str = "orders",
    alias: str | None = None,
    package_name: str = "pkg",
    database: str | None = "analytics",
    schema: str = "marts",
    materialized: str = "table",
    **extra: Any,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "unique_id": unique_id,
        "resource_type": "model",
        "name": name,
        "package_name": package_name,
        "database": database,
        "schema": schema,
        "alias": alias if alias is not None else name,
        "fqn": [package_name, name],
        "config": {"materialized": materialized},
        "columns": {},
        "tags": [],
        "meta": {},
        "description": "",
    }
    node.update(extra)
    return node


def _source(
    unique_id: str = "source.pkg.raw.customers",
    *,
    name: str = "customers",
    source_name: str = "raw",
    identifier: str | None = None,
    package_name: str = "pkg",
    database: str | None = "analytics",
    schema: str = "raw",
    **extra: Any,
) -> dict[str, Any]:
    src: dict[str, Any] = {
        "unique_id": unique_id,
        "resource_type": "source",
        "name": name,
        "source_name": source_name,
        "package_name": package_name,
        "database": database,
        "schema": schema,
        "identifier": identifier if identifier is not None else name,
        "fqn": [package_name, source_name, name],
        "columns": {},
        "tags": [],
        "meta": {},
        "description": "",
    }
    src.update(extra)
    return src


def _write_json(path: Path, document: dict[str, Any]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- A: loader / JSON tree ---


def test_load_valid_manifest_json(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "manifest.json", _minimal_v12_manifest())
    loaded = load_dbt_manifest(path)
    assert loaded["metadata"]["dbt_schema_version"] == V12


def test_unsupported_suffix(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(DbtReadError) as exc:
        load_dbt_manifest(path)
    assert exc.value.errors[0].code == CODE_READ


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DbtReadError) as exc:
        load_dbt_manifest(tmp_path / "missing.json")
    assert exc.value.errors[0].code == CODE_READ


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DbtParseError) as exc:
        load_dbt_manifest(path)
    assert exc.value.errors[0].code == CODE_PARSE


def test_nan_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"metadata": NaN}', encoding="utf-8")
    with pytest.raises(DbtParseError):
        load_dbt_manifest(path)


def test_infinity_rejected(tmp_path: Path) -> None:
    path = tmp_path / "inf.json"
    path.write_text('{"metadata": Infinity}', encoding="utf-8")
    with pytest.raises(DbtParseError):
        load_dbt_manifest(path)


def test_neg_infinity_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ninf.json"
    path.write_text('{"metadata": -Infinity}', encoding="utf-8")
    with pytest.raises(DbtParseError):
        load_dbt_manifest(path)


def test_root_non_object_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(DbtParseError) as exc:
        load_dbt_manifest(path)
    assert "mapping" in exc.value.errors[0].message


def test_load_returns_independent_copy(tmp_path: Path) -> None:
    doc = _minimal_v12_manifest(nodes={"model.pkg.a": _model()})
    path = _write_json(tmp_path / "manifest.json", doc)
    loaded = load_dbt_manifest(path)
    loaded["nodes"]["model.pkg.a"]["name"] = "mutated"
    loaded2 = load_dbt_manifest(path)
    assert loaded2["nodes"]["model.pkg.a"]["name"] == "orders"


def test_direct_mapping_cycle_rejected() -> None:
    doc: dict[str, Any] = {}
    doc["self"] = doc
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].code == CODE_VALIDATION
    assert "finite JSON tree" in exc.value.errors[0].message


def test_max_depth_rejected() -> None:
    nested: Any = "leaf"
    for _ in range(70):
        nested = {"x": nested}
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(nested)
    assert "finite JSON tree" in exc.value.errors[0].message


def test_non_string_mapping_key_rejected() -> None:
    doc = _minimal_v12_manifest()
    # Build via Python Mapping with int key after copy would fail; inject via wrap.
    bad: dict[Any, Any] = {"metadata": doc["metadata"], "nodes": {1: "x"}}
    bad.update({k: doc[k] for k in ("sources", "parent_map", "disabled")})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(bad)
    assert "finite JSON tree" in exc.value.errors[0].message


def test_unsupported_json_type_rejected() -> None:
    doc = _minimal_v12_manifest()
    doc["nodes"] = {"model.pkg.a": _model()}
    # bytes is not JSON-compatible
    doc["nodes"]["model.pkg.a"]["meta"] = {"bin": b"x"}  # type: ignore[assignment]
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert "finite JSON tree" in exc.value.errors[0].message


# --- B: version contract ---


def test_exact_v12_uri_accepted() -> None:
    validated = validate_dbt_manifest(_minimal_v12_manifest())
    assert validated["metadata"]["dbt_schema_version"] == V12


def test_missing_metadata() -> None:
    doc = _minimal_v12_manifest()
    del doc["metadata"]
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/metadata"


def test_missing_dbt_schema_version() -> None:
    doc = _minimal_v12_manifest()
    del doc["metadata"]["dbt_schema_version"]
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/metadata/dbt_schema_version"


def test_v11_rejected() -> None:
    doc = _minimal_v12_manifest()
    doc["metadata"]["dbt_schema_version"] = "https://schemas.getdbt.com/dbt/manifest/v11.json"
    with pytest.raises(DbtUnsupportedManifestVersionError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].code == CODE_UNSUPPORTED_MANIFEST_VERSION
    assert exc.value.errors[0].path == "/metadata/dbt_schema_version"


def test_fake_v13_rejected() -> None:
    doc = _minimal_v12_manifest()
    doc["metadata"]["dbt_schema_version"] = "https://schemas.getdbt.com/dbt/manifest/v13.json"
    with pytest.raises(DbtUnsupportedManifestVersionError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].code == CODE_UNSUPPORTED_MANIFEST_VERSION


def test_dbt_version_missing_rejected() -> None:
    doc = _minimal_v12_manifest()
    del doc["metadata"]["dbt_version"]
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/metadata/dbt_version"


def test_dbt_version_empty_rejected() -> None:
    doc = _minimal_v12_manifest()
    doc["metadata"]["dbt_version"] = "  "
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/metadata/dbt_version"


def test_validate_returns_copy_not_mutating_input() -> None:
    doc = _minimal_v12_manifest()
    validated = validate_dbt_manifest(doc)
    validated["nodes"]["x"] = 1
    assert "x" not in doc["nodes"]


def test_extra_top_level_keys_allowed() -> None:
    doc = _minimal_v12_manifest(macros={}, child_map={}, exposures={})
    validated = validate_dbt_manifest(doc)
    assert "macros" in validated


# --- C: structural subset ---


def test_nodes_wrong_type() -> None:
    doc = _minimal_v12_manifest(nodes=[])
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/nodes"


def test_sources_wrong_type() -> None:
    doc = _minimal_v12_manifest(sources="x")
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/sources"


def test_parent_map_wrong_type() -> None:
    doc = _minimal_v12_manifest(parent_map=[])
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/parent_map"


def test_disabled_array_rejected() -> None:
    doc = _minimal_v12_manifest(disabled=[])
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/disabled"


def test_disabled_mapping_accepted() -> None:
    doc = _minimal_v12_manifest(disabled={"model.pkg.x": [{"unique_id": "model.pkg.x"}]})
    validate_dbt_manifest(doc)


def test_node_key_must_equal_unique_id() -> None:
    node = _model(unique_id="model.pkg.orders")
    doc = _minimal_v12_manifest(nodes={"model.pkg.other": node})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/nodes/model.pkg.other"
    assert "unique_id" in exc.value.errors[0].message


def test_malformed_model_alias_pointer() -> None:
    node = _model()
    del node["alias"]
    doc = _minimal_v12_manifest(nodes={node["unique_id"]: node})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/nodes/model.pkg.orders/alias"


def test_malformed_source_identifier_pointer() -> None:
    src = _source()
    del src["identifier"]
    doc = _minimal_v12_manifest(sources={src["unique_id"]: src})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/sources/source.pkg.raw.customers/identifier"


def test_malformed_parent_map_value_pointer() -> None:
    doc = _minimal_v12_manifest(parent_map={"model.pkg.a": "model.pkg.b"})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/parent_map/model.pkg.a"


def test_malformed_parent_map_ref_pointer() -> None:
    doc = _minimal_v12_manifest(parent_map={"model.pkg.a": [""]})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/parent_map/model.pkg.a/0"


def test_column_key_name_mismatch() -> None:
    node = _model(
        columns={"id": {"name": "order_id", "data_type": "int"}},
    )
    doc = _minimal_v12_manifest(nodes={node["unique_id"]: node})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert "ColumnInfo.name" in exc.value.errors[0].message


def test_unsupported_node_types_ignored_structurally() -> None:
    doc = _minimal_v12_manifest(
        nodes={
            "test.pkg.not_null_orders_id": {
                "unique_id": "test.pkg.not_null_orders_id",
                "resource_type": "test",
                "name": "not_null_orders_id",
                "package_name": "pkg",
            },
            "model.pkg.orders": _model(),
        }
    )
    validated = validate_dbt_manifest(doc)
    assert "test.pkg.not_null_orders_id" in validated["nodes"]


def test_valid_model_and_source_pass() -> None:
    model = _model()
    source = _source()
    doc = _minimal_v12_manifest(
        nodes={model["unique_id"]: model},
        sources={source["unique_id"]: source},
        parent_map={model["unique_id"]: [source["unique_id"]]},
    )
    validate_dbt_manifest(doc)


def test_missing_top_level_nodes() -> None:
    doc = _minimal_v12_manifest()
    del doc["nodes"]
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(doc)
    assert exc.value.errors[0].path == "/nodes"


def test_fail_fast_diagnostics_independent_of_nodes_insertion_order() -> None:
    bad_a = _model(unique_id="model.pkg.a", name="a", alias="a")
    del bad_a["alias"]
    bad_b = _model(unique_id="model.pkg.b", name="b", alias="b")
    del bad_b["alias"]

    def diagnostic_for(nodes: dict[str, Any]) -> DbtDiagnostic:
        with pytest.raises(DbtValidationError) as exc:
            validate_dbt_manifest(_minimal_v12_manifest(nodes=nodes))
        return exc.value.errors[0]

    first = diagnostic_for({"model.pkg.a": bad_a, "model.pkg.b": bad_b})
    second = diagnostic_for({"model.pkg.b": bad_b, "model.pkg.a": bad_a})
    assert first == second
    assert first.path == "/nodes/model.pkg.a/alias"


def test_fail_fast_diagnostics_independent_of_columns_insertion_order() -> None:
    def diagnostic_for(columns: dict[str, Any]) -> DbtDiagnostic:
        model = _model(columns=columns)
        with pytest.raises(DbtValidationError) as exc:
            validate_dbt_manifest(_minimal_v12_manifest(nodes={model["unique_id"]: model}))
        return exc.value.errors[0]

    cols_ab = {
        "a": {"name": "a", "data_type": 1},
        "b": {"name": "b", "data_type": 2},
    }
    cols_ba = {
        "b": {"name": "b", "data_type": 2},
        "a": {"name": "a", "data_type": 1},
    }
    first = diagnostic_for(cols_ab)
    second = diagnostic_for(cols_ba)
    assert first == second
    assert first.path == "/nodes/model.pkg.orders/columns/a/data_type"


def test_fail_fast_diagnostics_independent_of_parent_map_insertion_order() -> None:
    def diagnostic_for(parent_map: dict[str, Any]) -> DbtDiagnostic:
        with pytest.raises(DbtValidationError) as exc:
            validate_dbt_manifest(_minimal_v12_manifest(parent_map=parent_map))
        return exc.value.errors[0]

    first = diagnostic_for({"model.pkg.a": "bad", "model.pkg.b": "also-bad"})
    second = diagnostic_for({"model.pkg.b": "also-bad", "model.pkg.a": "bad"})
    assert first == second
    assert first.path == "/parent_map/model.pkg.a"


def test_fail_fast_diagnostics_independent_of_disabled_insertion_order() -> None:
    def diagnostic_for(disabled: dict[str, Any]) -> DbtDiagnostic:
        with pytest.raises(DbtValidationError) as exc:
            validate_dbt_manifest(_minimal_v12_manifest(disabled=disabled))
        return exc.value.errors[0]

    first = diagnostic_for({"model.pkg.a": "bad", "model.pkg.b": "also-bad"})
    second = diagnostic_for({"model.pkg.b": "also-bad", "model.pkg.a": "bad"})
    assert first == second
    assert first.path == "/disabled/model.pkg.a"


def test_materialized_bool_rejected() -> None:
    node = _model(config={"materialized": False})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(_minimal_v12_manifest(nodes={node["unique_id"]: node}))
    assert exc.value.errors[0].path == "/nodes/model.pkg.orders/config/materialized"
    assert exc.value.errors[0].code == CODE_VALIDATION


def test_materialized_list_rejected() -> None:
    node = _model(config={"materialized": ["ephemeral"]})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(_minimal_v12_manifest(nodes={node["unique_id"]: node}))
    assert exc.value.errors[0].path == "/nodes/model.pkg.orders/config/materialized"


def test_materialized_null_rejected() -> None:
    node = _model(config={"materialized": None})
    with pytest.raises(DbtValidationError) as exc:
        validate_dbt_manifest(_minimal_v12_manifest(nodes={node["unique_id"]: node}))
    assert exc.value.errors[0].path == "/nodes/model.pkg.orders/config/materialized"


def test_validate_does_not_raise_raw_recursion_error() -> None:
    doc: dict[str, Any] = {"a": {}}
    cur = doc["a"]
    for _ in range(100):
        nxt: dict[str, Any] = {}
        cur["n"] = nxt
        cur = nxt
    with pytest.raises(DbtValidationError):
        validate_dbt_manifest(doc)


def _nodes_by_kind(graph: Any, kind: str) -> list[Any]:
    return [node for node in graph.nodes if node.identity.kind == kind]


def _edges_by_kind(graph: Any, kind: str) -> list[Any]:
    return [edge for edge in graph.edges if edge.kind == kind]


# --- D: relation identity ---


def test_relation_hierarchy_database_schema_table() -> None:
    model = _model(alias="orders_tbl", database="Analytics", schema="Marts")
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    tables = _nodes_by_kind(graph, NODE_KIND_TABLE)
    assert len(tables) == 1
    table = tables[0]
    assert table.identity.logical_id == "orders_tbl"
    assert table.name == "orders_tbl"
    dataset = table.identity.parent
    assert dataset is not None
    assert dataset.kind == NODE_KIND_DATASET
    assert dataset.logical_id == "Marts"
    ds = dataset.parent
    assert ds is not None
    assert ds.kind == NODE_KIND_DATA_SOURCE
    assert ds.logical_id == "Analytics"


def test_dbt_name_is_not_table_identity() -> None:
    model = _model(name="orders", alias="orders_v2")
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert table.identity.logical_id == "orders_v2"
    assert table.identity.logical_id != "orders"
    assert table.attributes_canonical  # has attrs including dbt_name


def test_unique_id_is_provenance_not_identity() -> None:
    model = _model()
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert table.identity.logical_id != model["unique_id"]
    assert table.provenance[0].source_ref == model["unique_id"]
    assert table.provenance[0].provider_type == "dbt"
    attrs = table.to_dict()["attributes"]
    assert "unique_id" not in attrs


def test_case_preserved_in_identifiers() -> None:
    model = _model(database="Db", schema="Sch", alias="Tbl")
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert table.identity.logical_id == "Tbl"
    assert table.identity.parent is not None
    assert table.identity.parent.logical_id == "Sch"
    assert table.identity.parent.parent is not None
    assert table.identity.parent.parent.logical_id == "Db"


def test_no_relation_name_parsing() -> None:
    model = _model(
        alias="orders",
        relation_name='"analytics"."marts"."something_else"',
    )
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert table.identity.logical_id == "orders"
    assert "relation_name" not in table.to_dict()["attributes"]


def test_same_physical_projection_rule_for_model_and_source() -> None:
    model = _model(
        unique_id="model.pkg.customers_model",
        name="customers_model",
        alias="customers",
        database="analytics",
        schema="raw",
    )
    source = _source(
        unique_id="source.pkg.raw.customers",
        name="customers_src",
        identifier="customers",
        database="analytics",
        schema="raw",
    )
    # Compatible merge would require identical material payload; conflicting attrs -> error.
    with pytest.raises(DbtMappingError):
        map_dbt_manifest(
            _minimal_v12_manifest(
                nodes={model["unique_id"]: model},
                sources={source["unique_id"]: source},
            ),
            namespace=NS,
        )


# --- E: default_database ---


def test_explicit_database_wins_over_default() -> None:
    model = _model(database="explicit_db")
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
        default_database="default_db",
    )
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert table.identity.parent is not None
    assert table.identity.parent.parent is not None
    assert table.identity.parent.parent.logical_id == "explicit_db"


def test_null_database_uses_default() -> None:
    model = _model(database=None)
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
        default_database="fallback_db",
    )
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert table.identity.parent is not None
    assert table.identity.parent.parent is not None
    assert table.identity.parent.parent.logical_id == "fallback_db"


def test_null_database_without_default_errors() -> None:
    model = _model(database=None)
    with pytest.raises(DbtMappingError) as exc:
        map_dbt_manifest(
            _minimal_v12_manifest(nodes={model["unique_id"]: model}),
            namespace=NS,
        )
    assert exc.value.errors[0].code == CODE_MAPPING
    assert exc.value.errors[0].path == "/nodes/model.pkg.orders/database"


def test_invalid_default_database_errors() -> None:
    with pytest.raises(DbtMappingError) as exc:
        map_dbt_manifest(_minimal_v12_manifest(), namespace=NS, default_database="  ")
    assert "default_database" in exc.value.errors[0].message


def test_changing_default_database_changes_identity() -> None:
    model = _model(database=None)
    doc = _minimal_v12_manifest(nodes={model["unique_id"]: model})
    g1 = map_dbt_manifest(doc, namespace=NS, default_database="db_a")
    g2 = map_dbt_manifest(doc, namespace=NS, default_database="db_b")
    assert g1.content_identity() != g2.content_identity()


# --- F: model mapping ---


def test_standard_model_maps_to_table_with_provenance() -> None:
    model = _model(
        description="Orders mart",
        tags=["b", "a", "a"],
        meta={"owner": "data"},
        config={"materialized": "incremental"},
    )
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert table.description == "Orders mart"
    attrs = table.to_dict()["attributes"]
    assert attrs["materialized"] == "incremental"
    assert attrs["tags"] == ["a", "b"]
    assert attrs["meta"] == {"owner": "data"}
    assert attrs["dbt_name"] == "orders"
    assert table.provenance[0].observation_mode == "declared"
    assert table.provenance[0].source_version == "1.10.0"


def test_volatile_fields_do_not_affect_graph() -> None:
    model = _model()
    a = _minimal_v12_manifest(nodes={model["unique_id"]: model})
    b = _minimal_v12_manifest(nodes={model["unique_id"]: dict(model)})
    b["metadata"]["generated_at"] = "2099-01-01T00:00:00Z"
    b["metadata"]["invocation_id"] = "other"
    b["metadata"]["invocation_started_at"] = "x"
    b["metadata"]["run_started_at"] = "y"
    model_b = dict(model)
    model_b["raw_code"] = "select 1"
    model_b["compiled_code"] = "select 1"
    model_b["path"] = "models/other.sql"
    model_b["original_file_path"] = "models/other.sql"
    model_b["checksum"] = {"name": "sha256", "checksum": "abc"}
    b["nodes"] = {model["unique_id"]: model_b}
    assert (
        map_dbt_manifest(a, namespace=NS).content_identity()
        == map_dbt_manifest(b, namespace=NS).content_identity()
    )


# --- G: source mapping ---


def test_source_uses_identifier_as_table_identity() -> None:
    source = _source(
        name="customers_logical",
        source_name="raw",
        identifier="customers_phys",
        loader="fivetran",
        description="Customers",
    )
    graph = map_dbt_manifest(
        _minimal_v12_manifest(sources={source["unique_id"]: source}),
        namespace=NS,
    )
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert table.identity.logical_id == "customers_phys"
    assert table.name == "customers_phys"
    attrs = table.to_dict()["attributes"]
    assert attrs["source_name"] == "raw"
    assert attrs["dbt_name"] == "customers_logical"
    assert attrs["loader"] == "fivetran"
    assert table.provenance[0].source_ref == source["unique_id"]


# --- H: ephemeral ---


def test_ephemeral_maps_to_transformation_not_table() -> None:
    model = _model(materialized="ephemeral", database=None)
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    assert _nodes_by_kind(graph, NODE_KIND_TABLE) == []
    assert _nodes_by_kind(graph, NODE_KIND_DATA_SOURCE) == []
    transforms = _nodes_by_kind(graph, NODE_KIND_TRANSFORMATION)
    assert len(transforms) == 1
    expected = canonical_json_bytes(["pkg", ["pkg", "orders"]]).decode("utf-8")
    assert transforms[0].identity.logical_id == expected
    assert transforms[0].name == "orders"
    assert transforms[0].identity.logical_id != model["unique_id"]


def test_custom_materialization_string_is_relation_backed() -> None:
    model = _model(materialized="custom_incremental")
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    assert _nodes_by_kind(graph, NODE_KIND_TRANSFORMATION) == []
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert table.to_dict()["attributes"]["materialized"] == "custom_incremental"


def test_ephemeral_columns_parent_transformation() -> None:
    model = _model(
        materialized="ephemeral",
        database=None,
        columns={"id": {"name": "id", "data_type": "int"}},
    )
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    col = _nodes_by_kind(graph, NODE_KIND_COLUMN)[0]
    assert col.identity.parent is not None
    assert col.identity.parent.kind == NODE_KIND_TRANSFORMATION


def test_dependencies_through_ephemeral_remain_first_order() -> None:
    source = _source()
    ephemeral = _model(
        unique_id="model.pkg.staging",
        name="staging",
        materialized="ephemeral",
        database=None,
    )
    downstream = _model(unique_id="model.pkg.mart", name="mart", alias="mart")
    doc = _minimal_v12_manifest(
        nodes={
            ephemeral["unique_id"]: ephemeral,
            downstream["unique_id"]: downstream,
        },
        sources={source["unique_id"]: source},
        parent_map={
            ephemeral["unique_id"]: [source["unique_id"]],
            downstream["unique_id"]: [ephemeral["unique_id"]],
        },
    )
    graph = map_dbt_manifest(doc, namespace=NS)
    deps = _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON)
    assert len(deps) == 2
    # No transitive mart -> source edge
    sources = {e.source for e in deps}
    targets = {e.target for e in deps}
    mart = next(
        n.identity for n in graph.nodes if n.identity.kind == NODE_KIND_TABLE and n.name == "mart"
    )
    staging = next(n.identity for n in graph.nodes if n.identity.kind == NODE_KIND_TRANSFORMATION)
    customers = next(
        n.identity
        for n in graph.nodes
        if n.identity.kind == NODE_KIND_TABLE and n.name == "customers"
    )
    assert (mart, staging) in {(e.source, e.target) for e in deps}
    assert (staging, customers) in {(e.source, e.target) for e in deps}
    assert (mart, customers) not in {(e.source, e.target) for e in deps}
    assert sources and targets


# --- I: columns ---


def test_model_and_source_columns() -> None:
    model = _model(
        columns={
            "id": {
                "name": "id",
                "data_type": "integer",
                "description": "pk",
                "tags": ["z", "a"],
                "meta": {"k": 1},
                "quote": True,
                "constraints": [{"type": "not_null"}, {"type": "unique"}],
            }
        }
    )
    source = _source(
        columns={"email": {"name": "email", "data_type": "text"}},
    )
    graph = map_dbt_manifest(
        _minimal_v12_manifest(
            nodes={model["unique_id"]: model},
            sources={source["unique_id"]: source},
        ),
        namespace=NS,
    )
    cols = {c.identity.logical_id: c for c in _nodes_by_kind(graph, NODE_KIND_COLUMN)}
    assert set(cols) == {"id", "email"}
    assert cols["id"].description == "pk"
    attrs = cols["id"].to_dict()["attributes"]
    assert attrs["data_type"] == "integer"
    assert attrs["tags"] == ["a", "z"]
    assert attrs["quote"] is True
    assert attrs["constraints"] == [{"type": "not_null"}, {"type": "unique"}]
    contains = _edges_by_kind(graph, EDGE_KIND_CONTAINS)
    assert any(e.target.kind == NODE_KIND_COLUMN for e in contains)
    assert not any(
        e.kind == EDGE_KIND_DEPENDS_ON and e.source.kind == NODE_KIND_COLUMN for e in graph.edges
    )


def test_column_tags_permutation_invariant() -> None:
    def doc(tags: list[str]) -> dict[str, Any]:
        model = _model(columns={"id": {"name": "id", "tags": tags}})
        return _minimal_v12_manifest(nodes={model["unique_id"]: model})

    assert (
        map_dbt_manifest(doc(["b", "a"]), namespace=NS).content_identity()
        == map_dbt_manifest(doc(["a", "b"]), namespace=NS).content_identity()
    )


# --- J: dependencies ---


def test_parent_map_creates_child_depends_on_parent() -> None:
    source = _source()
    model = _model()
    graph = map_dbt_manifest(
        _minimal_v12_manifest(
            nodes={model["unique_id"]: model},
            sources={source["unique_id"]: source},
            parent_map={model["unique_id"]: [source["unique_id"]]},
        ),
        namespace=NS,
    )
    deps = _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON)
    assert len(deps) == 1
    assert deps[0].source.kind == NODE_KIND_TABLE
    assert deps[0].source.logical_id == "orders"
    assert deps[0].target.logical_id == "customers"
    assert deps[0].provenance[0].source_ref == model["unique_id"]


def test_dependency_order_and_duplicates_irrelevant() -> None:
    a = _model(unique_id="model.pkg.a", name="a", alias="a")
    b = _model(unique_id="model.pkg.b", name="b", alias="b")
    c = _model(unique_id="model.pkg.c", name="c", alias="c")
    doc1 = _minimal_v12_manifest(
        nodes={x["unique_id"]: x for x in (a, b, c)},
        parent_map={c["unique_id"]: [a["unique_id"], b["unique_id"], a["unique_id"]]},
    )
    doc2 = _minimal_v12_manifest(
        nodes={x["unique_id"]: x for x in (a, b, c)},
        parent_map={c["unique_id"]: [b["unique_id"], a["unique_id"]]},
    )
    assert (
        map_dbt_manifest(doc1, namespace=NS).content_identity()
        == map_dbt_manifest(doc2, namespace=NS).content_identity()
    )
    deps = _edges_by_kind(map_dbt_manifest(doc1, namespace=NS), EDGE_KIND_DEPENDS_ON)
    assert len(deps) == 2


def test_child_map_and_nodal_depends_on_ignored() -> None:
    source = _source()
    model = _model(
        depends_on={"nodes": ["seed.pkg.ignored"], "macros": []},
    )
    # parent_map empty => no depends_on edges even if nodal depends_on / child_map present
    graph = map_dbt_manifest(
        _minimal_v12_manifest(
            nodes={model["unique_id"]: model},
            sources={source["unique_id"]: source},
            parent_map={},
            child_map={source["unique_id"]: [model["unique_id"]]},
        ),
        namespace=NS,
    )
    assert _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON) == []


def test_no_transitive_edges() -> None:
    a = _model(unique_id="model.pkg.a", name="a", alias="a")
    b = _model(unique_id="model.pkg.b", name="b", alias="b")
    c = _model(unique_id="model.pkg.c", name="c", alias="c")
    graph = map_dbt_manifest(
        _minimal_v12_manifest(
            nodes={x["unique_id"]: x for x in (a, b, c)},
            parent_map={
                b["unique_id"]: [a["unique_id"]],
                c["unique_id"]: [b["unique_id"]],
            },
        ),
        namespace=NS,
    )
    pairs = {
        (e.source.logical_id, e.target.logical_id)
        for e in _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON)
    }
    assert pairs == {("b", "a"), ("c", "b")}


# --- K: unsupported / disabled ---


def test_tests_and_analyses_ignored() -> None:
    model = _model()
    doc = _minimal_v12_manifest(
        nodes={
            model["unique_id"]: model,
            "test.pkg.t": {
                "unique_id": "test.pkg.t",
                "resource_type": "test",
                "name": "t",
                "package_name": "pkg",
            },
            "analysis.pkg.a": {
                "unique_id": "analysis.pkg.a",
                "resource_type": "analysis",
                "name": "a",
                "package_name": "pkg",
            },
        }
    )
    graph = map_dbt_manifest(doc, namespace=NS)
    assert len(_nodes_by_kind(graph, NODE_KIND_TABLE)) == 1


def test_disabled_resources_not_mapped() -> None:
    model = _model()
    disabled_model = _model(unique_id="model.pkg.disabled", name="disabled", alias="disabled")
    graph = map_dbt_manifest(
        _minimal_v12_manifest(
            nodes={model["unique_id"]: model},
            disabled={disabled_model["unique_id"]: [disabled_model]},
            parent_map={model["unique_id"]: [disabled_model["unique_id"]]},
        ),
        namespace=NS,
    )
    assert all(n.identity.logical_id != "disabled" for n in graph.nodes)
    assert _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON) == []


def test_empty_disabled_list_does_not_hide_unresolved_parent() -> None:
    model = _model()
    with pytest.raises(DbtMappingError) as exc:
        map_dbt_manifest(
            _minimal_v12_manifest(
                nodes={model["unique_id"]: model},
                disabled={"model.pkg.x": []},
                parent_map={model["unique_id"]: ["model.pkg.x"]},
            ),
            namespace=NS,
        )
    assert "unresolved" in exc.value.errors[0].message


def test_nonempty_disabled_list_omits_dependency_without_error() -> None:
    model = _model()
    graph = map_dbt_manifest(
        _minimal_v12_manifest(
            nodes={model["unique_id"]: model},
            disabled={"model.pkg.x": [{"unique_id": "model.pkg.x"}]},
            parent_map={model["unique_id"]: ["model.pkg.x"]},
        ),
        namespace=NS,
    )
    assert _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON) == []


def test_unrelated_empty_disabled_does_not_change_hash() -> None:
    model = _model()
    source = _source()
    base = _minimal_v12_manifest(
        nodes={model["unique_id"]: model},
        sources={source["unique_id"]: source},
        parent_map={model["unique_id"]: [source["unique_id"]]},
    )
    with_empty = dict(base)
    with_empty["disabled"] = {"model.pkg.unrelated": []}
    assert (
        map_dbt_manifest(base, namespace=NS).content_identity()
        == map_dbt_manifest(with_empty, namespace=NS).content_identity()
    )


def test_unsupported_parent_dependency_skipped() -> None:
    model = _model()
    graph = map_dbt_manifest(
        _minimal_v12_manifest(
            nodes={model["unique_id"]: model},
            parent_map={model["unique_id"]: ["seed.pkg.seed", "macro.pkg.m"]},
        ),
        namespace=NS,
    )
    assert _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON) == []


def test_missing_mapped_model_parent_errors() -> None:
    model = _model()
    with pytest.raises(DbtMappingError) as exc:
        map_dbt_manifest(
            _minimal_v12_manifest(
                nodes={model["unique_id"]: model},
                parent_map={model["unique_id"]: ["model.pkg.missing"]},
            ),
            namespace=NS,
        )
    assert "unresolved" in exc.value.errors[0].message


def test_normal_manifest_with_macros_does_not_fail() -> None:
    model = _model()
    doc = _minimal_v12_manifest(
        nodes={model["unique_id"]: model},
        macros={"macro.pkg.m": {"unique_id": "macro.pkg.m", "resource_type": "macro"}},
    )
    map_dbt_manifest(doc, namespace=NS)


# --- L: determinism ---


def test_dict_and_tag_permutations_stable_hash() -> None:
    model = _model(tags=["b", "a"], columns={"id": {"name": "id"}, "name": {"name": "name"}})
    source = _source()
    base_nodes = {model["unique_id"]: model}
    base_sources = {source["unique_id"]: source}
    parent_map = {model["unique_id"]: [source["unique_id"]]}

    g1 = map_dbt_manifest(
        _minimal_v12_manifest(nodes=base_nodes, sources=base_sources, parent_map=parent_map),
        namespace=NS,
    )

    model2 = _model(tags=["a", "b"], columns={"name": {"name": "name"}, "id": {"name": "id"}})
    g2 = map_dbt_manifest(
        _minimal_v12_manifest(
            nodes={model2["unique_id"]: model2},
            sources={source["unique_id"]: source},
            parent_map=parent_map,
        ),
        namespace=NS,
    )
    assert g1.content_identity() == g2.content_identity()
    assert g1.to_dict() == g2.to_dict()


def test_file_path_independence(tmp_path: Path) -> None:
    model = _model()
    doc = _minimal_v12_manifest(nodes={model["unique_id"]: model})
    p1 = tmp_path / "a" / "manifest.json"
    p2 = tmp_path / "b" / "manifest.json"
    p1.parent.mkdir(parents=True, exist_ok=True)
    p2.parent.mkdir(parents=True, exist_ok=True)
    _write_json(p1, doc)
    _write_json(p2, doc)
    assert (
        load_dbt_graph(p1, namespace=NS).content_identity()
        == load_dbt_graph(p2, namespace=NS).content_identity()
    )


def test_dbt_version_in_provenance_affects_hash() -> None:
    model = _model()
    a = _minimal_v12_manifest(nodes={model["unique_id"]: model})
    b = _minimal_v12_manifest(nodes={model["unique_id"]: model})
    b["metadata"]["dbt_version"] = "1.11.0"
    assert (
        map_dbt_manifest(a, namespace=NS).content_identity()
        != map_dbt_manifest(b, namespace=NS).content_identity()
    )


# --- M: regression / scope ---


def test_namespace_required() -> None:
    with pytest.raises(DbtMappingError) as exc:
        map_dbt_manifest(_minimal_v12_manifest(), namespace="  ")
    assert exc.value.errors[0].message == "namespace is required"


def test_namespace_not_default_dbt() -> None:
    model = _model()
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    assert all(n.identity.namespace == NS for n in graph.nodes)
    assert NS != "dbt"


def test_containers_have_empty_attrs_and_provenance() -> None:
    model = _model()
    graph = map_dbt_manifest(
        _minimal_v12_manifest(nodes={model["unique_id"]: model}),
        namespace=NS,
    )
    for node in graph.nodes:
        if node.identity.kind in {NODE_KIND_DATA_SOURCE, NODE_KIND_DATASET}:
            assert node.to_dict()["attributes"] == {}
            assert node.provenance == ()


def test_public_api_surface_is_small() -> None:
    import governance.integrations.dbt as dbt

    assert "SUPPORTED_MANIFEST_SCHEMA_URI" not in dbt.__all__
    assert "_copy_json_tree" not in dbt.__all__
    for name in (
        "load_dbt_manifest",
        "validate_dbt_manifest",
        "map_dbt_manifest",
        "load_dbt_graph",
        "DbtMappingError",
        "CODE_MAPPING",
    ):
        assert name in dbt.__all__


def test_package_version_is_120() -> None:
    assert __version__ == "1.2.0"
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert 'version = "1.2.0"' in pyproject.read_text(encoding="utf-8")


def test_no_new_runtime_dependencies_declared() -> None:
    text = (
        Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text(encoding="utf-8")
    )
    assert "dbt-core" not in text
    assert "dbt-common" not in text
