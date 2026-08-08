"""Tests for dbt Manifest v12 subset loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from governance.integrations.dbt import (
    CODE_PARSE,
    CODE_READ,
    CODE_UNSUPPORTED_MANIFEST_VERSION,
    CODE_VALIDATION,
    DbtDiagnostic,
    DbtParseError,
    DbtReadError,
    DbtUnsupportedManifestVersionError,
    DbtValidationError,
    load_dbt_manifest,
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



