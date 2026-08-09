"""Tests for ODCS v3.1.0 document validation and GovernanceGraph mapping."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema.validators import Draft202012Validator, validator_for

from governance import __version__
from governance.domain.graph import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    EDGE_KIND_GOVERNS,
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
)
from governance.integrations.odcs import (
    OdcsMappingError,
    OdcsParseError,
    OdcsReadError,
    OdcsSchemaError,
    OdcsUnsupportedVersionError,
    load_odcs_document,
    load_odcs_graph,
    map_odcs_document,
    validate_odcs_document,
)
from governance.integrations.odcs.schema import (
    ODCS_SCHEMA_SHA256,
    load_odcs_schema,
)

NS = "acme.commerce"


def _minimal_contract(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": "contract-orders",
        "version": "1.0.0",
        "status": "active",
        "name": "Orders Contract",
    }
    doc.update(overrides)
    return doc


def _write(path: Path, document: dict[str, Any], *, fmt: str = "yaml") -> Path:
    if fmt == "yaml":
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- A: Parse/read ---


def test_load_valid_yaml_and_json(tmp_path: Path) -> None:
    doc = _minimal_contract()
    yaml_path = _write(tmp_path / "c.odcs.yaml", doc, fmt="yaml")
    json_path = _write(tmp_path / "c.json", doc, fmt="json")
    assert load_odcs_document(yaml_path)["id"] == "contract-orders"
    assert load_odcs_document(json_path)["id"] == "contract-orders"


def test_load_odcs_yml_suffix(tmp_path: Path) -> None:
    path = _write(tmp_path / "c.odcs.yml", _minimal_contract(), fmt="yaml")
    assert load_odcs_document(path)["apiVersion"] == "v3.1.0"


def test_unsupported_suffix(tmp_path: Path) -> None:
    path = tmp_path / "c.txt"
    path.write_text("apiVersion: v3.1.0\n", encoding="utf-8")
    with pytest.raises(OdcsReadError) as exc:
        load_odcs_document(path)
    assert exc.value.errors[0].code == "odcs_read_error"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OdcsReadError):
        load_odcs_document(tmp_path / "missing.yaml")


def test_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(":\n  - invalid\n", encoding="utf-8")
    with pytest.raises(OdcsParseError) as exc:
        load_odcs_document(path)
    assert exc.value.errors[0].code == "odcs_parse_error"


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(OdcsParseError):
        load_odcs_document(path)


def test_unsafe_yaml_tag_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text("!!python/object/apply:os.system ['echo hi']\n", encoding="utf-8")
    with pytest.raises(OdcsParseError):
        load_odcs_document(path)


def test_root_non_object_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(OdcsParseError) as exc:
        load_odcs_document(path)
    assert "mapping" in exc.value.errors[0].message


def test_load_does_not_mutate_and_returns_copy(tmp_path: Path) -> None:
    doc = _minimal_contract(tags=["a"])
    path = _write(tmp_path / "c.yaml", doc)
    loaded = load_odcs_document(path)
    loaded["tags"].append("b")
    loaded2 = load_odcs_document(path)
    assert loaded2["tags"] == ["a"]


# --- B: Version/schema ---


def test_api_version_v310_accepted() -> None:
    validated = validate_odcs_document(_minimal_contract())
    assert validated["apiVersion"] == "v3.1.0"


def test_missing_api_version() -> None:
    doc = _minimal_contract()
    del doc["apiVersion"]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert exc.value.errors[0].path == "/apiVersion"
    assert exc.value.errors[0].code == "odcs_schema_error"


def test_v302_rejected_as_unsupported() -> None:
    with pytest.raises(OdcsUnsupportedVersionError) as exc:
        validate_odcs_document(_minimal_contract(apiVersion="v3.0.2"))
    assert exc.value.errors[0].code == "odcs_unsupported_version"
    assert exc.value.errors[0].path == "/apiVersion"


def test_v22x_rejected_as_unsupported() -> None:
    with pytest.raises(OdcsUnsupportedVersionError) as exc:
        validate_odcs_document(_minimal_contract(apiVersion="v2.2.2"))
    assert exc.value.errors[0].code == "odcs_unsupported_version"


def test_wrong_kind() -> None:
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(_minimal_contract(kind="SomethingElse"))
    assert any(e.path == "/kind" for e in exc.value.errors)


def test_missing_version_and_status_have_distinct_paths() -> None:
    doc = _minimal_contract()
    del doc["version"]
    del doc["status"]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    by_path = {e.path: e for e in exc.value.errors}
    assert "/version" in by_path
    assert "/status" in by_path
    assert by_path["/version"].message == "missing required property"
    assert by_path["/status"].message == "missing required property"


def test_nested_missing_name_has_exact_path() -> None:
    doc = _minimal_contract(schema=[{}])
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert any(
        e.path == "/schema/0/name" and e.message == "missing required property"
        for e in exc.value.errors
    )


def test_unknown_top_level_properties_are_actionable_and_deduped() -> None:
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(_minimal_contract(foo=1, bar=2))
    unknown = [e for e in exc.value.errors if e.message == "unknown property is not allowed"]
    paths = [e.path for e in unknown]
    assert paths.count("/foo") == 1
    assert paths.count("/bar") == 1
    assert paths == sorted(paths)


def test_unknown_nested_property_has_actionable_pointer() -> None:
    doc = _minimal_contract(schema=[{"name": "orders", "notReal": True}])
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert any(
        e.path == "/schema/0/notReal" and e.message == "unknown property is not allowed"
        for e in exc.value.errors
    )


def test_invalid_nested_property_type() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [{"name": "id", "required": "yes"}],
            }
        ]
    )
    with pytest.raises(OdcsSchemaError):
        validate_odcs_document(doc)


def test_diagnostics_ordered_by_path() -> None:
    doc = _minimal_contract(extraOne=1, extraTwo=2)
    del doc["status"]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    keys = [(e.path, e.code, e.message) for e in exc.value.errors]
    assert keys == sorted(keys)
    assert any(e.path == "/status" for e in exc.value.errors)
    assert any(e.path == "/extraOne" for e in exc.value.errors)
    assert any(e.path == "/extraTwo" for e in exc.value.errors)


def test_bundled_schema_loads_offline() -> None:
    schema = load_odcs_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2019-09/schema"
    assert "apiVersion" in schema["properties"]


def test_validator_follows_2019_09_not_hardcoded_2020_12() -> None:
    schema = load_odcs_schema()
    cls = validator_for(schema)
    assert cls is not Draft202012Validator
    assert "201909" in cls.__name__ or "2019" in cls.__name__.lower()


def test_cyclic_mapping_rejected_without_recursion_error() -> None:
    doc: dict[str, Any] = _minimal_contract()
    doc["self"] = doc  # type: ignore[assignment]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert exc.value.errors[0].message == "document is not a finite JSON tree"


def test_cyclic_list_rejected() -> None:
    lst: list[Any] = []
    lst.append(lst)
    doc = _minimal_contract(schema=lst)
    with pytest.raises(OdcsSchemaError):
        validate_odcs_document(doc)


def test_json_nan_rejected_as_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text(
        '{"apiVersion":"v3.1.0","kind":"DataContract","id":"x",'
        '"version":"1","status":"active","score":NaN}',
        encoding="utf-8",
    )
    with pytest.raises(OdcsParseError):
        load_odcs_document(path)


def test_json_infinity_rejected_as_parse_error(tmp_path: Path) -> None:
    for literal in ("Infinity", "-Infinity"):
        path = tmp_path / f"{literal}.json"
        path.write_text(
            '{"apiVersion":"v3.1.0","kind":"DataContract","id":"x",'
            f'"version":"1","status":"active","score":{literal}}}',
            encoding="utf-8",
        )
        with pytest.raises(OdcsParseError):
            load_odcs_document(path)


def test_direct_non_finite_float_rejected_as_schema_error() -> None:
    for value in (float("nan"), float("inf"), float("-inf")):
        doc = _minimal_contract()
        doc["tenant"] = value  # type: ignore[assignment]
        with pytest.raises(OdcsSchemaError) as exc:
            validate_odcs_document(doc)
        assert exc.value.errors[0].message == "document is not a finite JSON tree"


def test_yaml_non_finite_rejected_at_validation(tmp_path: Path) -> None:
    path = tmp_path / "nan.yaml"
    path.write_text(
        "apiVersion: v3.1.0\nkind: DataContract\nid: x\nversion: '1'\n"
        "status: active\nscore: .nan\n",
        encoding="utf-8",
    )
    loaded = load_odcs_document(path)
    with pytest.raises(OdcsSchemaError):
        validate_odcs_document(loaded)


def test_max_depth_64_rejected() -> None:
    nested: Any = "leaf"
    for _ in range(65):
        nested = {"child": nested}
    doc = _minimal_contract()
    doc["description"] = {"purpose": "x", "usage": nested}
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert exc.value.errors[0].message == "document is not a finite JSON tree"


def test_non_json_compatible_type_rejected() -> None:
    doc = _minimal_contract()
    doc["tenant"] = object()  # type: ignore[assignment]
    with pytest.raises(OdcsSchemaError) as exc:
        validate_odcs_document(doc)
    assert exc.value.errors[0].message == "document is not a finite JSON tree"


# --- C: Contract mapping ---


def test_exactly_one_contract_node() -> None:
    graph = map_odcs_document(_minimal_contract(), namespace=NS)
    contracts = [n for n in graph.nodes if n.identity.kind == NODE_KIND_CONTRACT]
    assert len(contracts) == 1
    assert contracts[0].identity.namespace == NS
    assert contracts[0].identity.logical_id == "contract-orders"
    assert contracts[0].identity.parent is None


def test_contract_name_fallback_to_id() -> None:
    doc = _minimal_contract()
    del doc["name"]
    graph = map_odcs_document(doc, namespace=NS)
    contract = next(n for n in graph.nodes if n.identity.kind == NODE_KIND_CONTRACT)
    assert contract.name == "contract-orders"


def test_purpose_description_rule() -> None:
    doc = _minimal_contract(
        description={
            "purpose": "Track orders",
            "usage": "Analytics",
            "limitations": "None",
        }
    )
    graph = map_odcs_document(doc, namespace=NS)
    contract = next(n for n in graph.nodes if n.identity.kind == NODE_KIND_CONTRACT)
    assert contract.description == "Track orders"
    assert contract.attributes_canonical  # has description object attrs


def test_contract_attributes_and_ownership_metadata() -> None:
    doc = _minimal_contract(
        domain="commerce",
        dataProduct="orders",
        tenant="ACME",
        tags=["b", "a", "a"],
        team={
            "name": "Data",
            "members": [
                {"username": "bob", "role": "owner"},
                {"username": "alice", "role": "steward"},
            ],
        },
        roles=[
            {"role": "reader", "description": "r"},
            {"role": "writer", "description": "w"},
        ],
    )
    graph = map_odcs_document(doc, namespace=NS)
    contract = next(n for n in graph.nodes if n.identity.kind == NODE_KIND_CONTRACT)
    attrs = contract.to_dict()["attributes"]
    assert attrs["api_version"] == "v3.1.0"
    assert attrs["contract_version"] == "1.0.0"
    assert attrs["status"] == "active"
    assert attrs["domain"] == "commerce"
    assert attrs["data_product"] == "orders"
    assert attrs["tenant"] == "acme"
    assert attrs["tags"] == ["a", "b"]
    assert "team" in attrs
    assert "roles" in attrs
    assert all(n.identity.kind != "person" for n in graph.nodes)


def test_odcs_provenance_exact() -> None:
    graph = map_odcs_document(_minimal_contract(), namespace=NS)
    for node in graph.nodes:
        assert len(node.provenance) == 1
        prov = node.provenance[0]
        assert prov.provider_type == "odcs"
        assert prov.source_ref == "contract-orders"
        assert prov.source_version == "1.0.0"
        assert prov.observation_mode == "declared"


# --- D/E: Dataset and fields ---


def _rich_contract() -> dict[str, Any]:
    return _minimal_contract(
        schema=[
            {
                "id": "ds-orders",
                "name": "orders",
                "physicalName": "orders_tbl",
                "logicalType": "object",
                "description": "Orders table",
                "tags": ["t2", "t1"],
                "properties": [
                    {
                        "id": "col-id",
                        "name": "order_id",
                        "logicalType": "string",
                        "required": True,
                        "classification": "internal",
                        "tags": ["pk"],
                    },
                    {
                        "name": "customer",
                        "logicalType": "object",
                        "logicalTypeOptions": {"required": ["city", "name"]},
                        "properties": [
                            {"name": "name", "logicalType": "string"},
                            {"name": "city", "logicalType": "string"},
                        ],
                    },
                    {
                        "name": "items",
                        "logicalType": "array",
                        "items": {
                            "logicalType": "object",
                            "properties": [
                                {"name": "sku", "logicalType": "string"},
                            ],
                        },
                    },
                ],
                "relationships": [
                    {
                        "from": "orders.order_id",
                        "to": "customers.id",
                    }
                ],
                "quality": [{"type": "text", "description": "order_id must be present"}],
            }
        ],
        servers=[
            {
                "server": "prod",
                "type": "postgresql",
                "host": "db",
                "port": 5432,
                "database": "x",
                "schema": "public",
            }
        ],
    )


def test_dataset_mapping_and_governs() -> None:
    graph = map_odcs_document(_rich_contract(), namespace=NS)
    datasets = [n for n in graph.nodes if n.identity.kind == NODE_KIND_DATASET]
    assert len(datasets) == 1
    ds = datasets[0]
    assert ds.identity.logical_id == "ds-orders"
    assert ds.identity.parent is None
    assert ds.to_dict()["attributes"]["physical_name"] == "orders_tbl"
    governs = [e for e in graph.edges if e.kind == EDGE_KIND_GOVERNS]
    assert len(governs) == 1
    assert governs[0].source.kind == NODE_KIND_CONTRACT
    assert governs[0].target.kind == NODE_KIND_DATASET


def test_dataset_id_fallback_to_name() -> None:
    doc = _minimal_contract(schema=[{"name": "orders", "logicalType": "object"}])
    graph = map_odcs_document(doc, namespace=NS)
    ds = next(n for n in graph.nodes if n.identity.kind == NODE_KIND_DATASET)
    assert ds.identity.logical_id == "orders"


def test_schema_input_order_irrelevant() -> None:
    a = _minimal_contract(
        schema=[
            {"id": "a", "name": "a", "logicalType": "object"},
            {"id": "b", "name": "b", "logicalType": "object"},
        ]
    )
    b = _minimal_contract(
        schema=[
            {"id": "b", "name": "b", "logicalType": "object"},
            {"id": "a", "name": "a", "logicalType": "object"},
        ]
    )
    g1 = map_odcs_document(a, namespace=NS)
    g2 = map_odcs_document(b, namespace=NS)
    assert g1.content_identity() == g2.content_identity()


def test_column_parent_contains_and_nested() -> None:
    graph = map_odcs_document(_rich_contract(), namespace=NS)
    columns = [n for n in graph.nodes if n.identity.kind == NODE_KIND_COLUMN]
    by_id = {n.identity.logical_id: n for n in columns}
    assert "col-id" in by_id
    assert by_id["col-id"].identity.parent is not None
    assert by_id["col-id"].identity.parent.kind == NODE_KIND_DATASET
    assert "customer" in by_id
    assert "name" in by_id
    assert by_id["name"].identity.parent.logical_id == "customer"
    assert "sku" in by_id
    assert by_id["sku"].identity.parent.logical_id == "items"
    contains = [e for e in graph.edges if e.kind == EDGE_KIND_CONTAINS]
    assert contains
    assert not any(e.kind == EDGE_KIND_DEPENDS_ON for e in graph.edges)


def test_field_order_irrelevant() -> None:
    base = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {"name": "a", "logicalType": "string"},
                    {"name": "b", "logicalType": "string"},
                ],
            }
        ]
    )
    swapped = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {"name": "b", "logicalType": "string"},
                    {"name": "a", "logicalType": "string"},
                ],
            }
        ]
    )
    assert (
        map_odcs_document(base, namespace=NS).content_identity()
        == map_odcs_document(swapped, namespace=NS).content_identity()
    )


def test_classification_change_alters_identity() -> None:
    base = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {"name": "ssn", "logicalType": "string", "classification": "public"}
                ],
            }
        ]
    )
    changed = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {"name": "ssn", "logicalType": "string", "classification": "confidential"}
                ],
            }
        ]
    )
    assert (
        map_odcs_document(base, namespace=NS).content_identity()
        != map_odcs_document(changed, namespace=NS).content_identity()
    )


# --- F: Provenance / path independence ---


def test_path_independence(tmp_path: Path) -> None:
    doc = _rich_contract()
    p1 = tmp_path / "a" / "c.yaml"
    p1.parent.mkdir(parents=True, exist_ok=True)
    _write(p1, doc)
    p2 = tmp_path / "b" / "other.yaml"
    p2.parent.mkdir(parents=True, exist_ok=True)
    _write(p2, doc)
    g1 = load_odcs_graph(p1, namespace=NS)
    g2 = load_odcs_graph(p2, namespace=NS)
    assert g1.content_identity() == g2.content_identity()
    assert g1.to_dict() == g2.to_dict()
    for node in g1.nodes:
        assert node.provenance[0].source_ref == "contract-orders"
        assert "tmp" not in node.provenance[0].source_ref
        assert "\\" not in node.provenance[0].source_ref


def test_yaml_json_equivalent(tmp_path: Path) -> None:
    doc = _rich_contract()
    y = _write(tmp_path / "c.yaml", doc, fmt="yaml")
    j = _write(tmp_path / "c.json", doc, fmt="json")
    assert (
        load_odcs_graph(y, namespace=NS).content_identity()
        == load_odcs_graph(j, namespace=NS).content_identity()
    )


# --- G: Metadata order / tenant ---


def test_tenant_casefold_same_identity() -> None:
    digests = {
        map_odcs_document(_minimal_contract(tenant=t), namespace=NS).content_identity().digest
        for t in ("ACME", "acme", "AcMe")
    }
    assert len(digests) == 1


def test_tenant_material_difference_changes_digest() -> None:
    a = map_odcs_document(_minimal_contract(tenant="acme"), namespace=NS)
    b = map_odcs_document(_minimal_contract(tenant="globex"), namespace=NS)
    assert a.content_identity() != b.content_identity()


def test_tags_permutation_same_hash() -> None:
    a = map_odcs_document(_minimal_contract(tags=["b", "a"]), namespace=NS)
    b = map_odcs_document(_minimal_contract(tags=["a", "b"]), namespace=NS)
    assert a.content_identity() == b.content_identity()


def test_team_members_and_roles_permutation() -> None:
    a = _minimal_contract(
        team={
            "members": [
                {"username": "bob"},
                {"username": "alice"},
            ]
        },
        roles=[{"role": "b"}, {"role": "a"}],
    )
    b = _minimal_contract(
        team={
            "members": [
                {"username": "alice"},
                {"username": "bob"},
            ]
        },
        roles=[{"role": "a"}, {"role": "b"}],
    )
    assert (
        map_odcs_document(a, namespace=NS).content_identity()
        == map_odcs_document(b, namespace=NS).content_identity()
    )


def test_logical_type_options_required_permutation() -> None:
    def make(required: list[str]) -> dict[str, Any]:
        return _minimal_contract(
            schema=[
                {
                    "name": "orders",
                    "properties": [
                        {
                            "name": "customer",
                            "logicalType": "object",
                            "logicalTypeOptions": {"required": required},
                            "properties": [
                                {"name": "name", "logicalType": "string"},
                                {"name": "city", "logicalType": "string"},
                            ],
                        }
                    ],
                }
            ]
        )

    assert (
        map_odcs_document(make(["city", "name"]), namespace=NS).content_identity()
        == map_odcs_document(make(["name", "city"]), namespace=NS).content_identity()
    )


def test_role_custom_properties_permutation() -> None:
    a = _minimal_contract(
        roles=[
            {
                "role": "reader",
                "customProperties": [
                    {"property": "b", "value": 2},
                    {"property": "a", "value": 1},
                ],
            }
        ]
    )
    b = _minimal_contract(
        roles=[
            {
                "role": "reader",
                "customProperties": [
                    {"property": "a", "value": 1},
                    {"property": "b", "value": 2},
                ],
            }
        ]
    )
    assert (
        map_odcs_document(a, namespace=NS).content_identity()
        == map_odcs_document(b, namespace=NS).content_identity()
    )


def test_dataset_and_column_setlike_permutation() -> None:
    def make(
        tags: list[str], auth: list[dict[str, str]], cps: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return _minimal_contract(
            schema=[
                {
                    "name": "orders",
                    "tags": tags,
                    "authoritativeDefinitions": auth,
                    "customProperties": cps,
                    "properties": [
                        {
                            "name": "id",
                            "logicalType": "string",
                            "tags": tags,
                            "authoritativeDefinitions": auth,
                            "customProperties": cps,
                        }
                    ],
                }
            ]
        )

    auth_a = [
        {"url": "https://b.example", "type": "businessDefinition"},
        {"url": "https://a.example", "type": "businessDefinition"},
    ]
    auth_b = list(reversed(auth_a))
    cps_a = [{"property": "b", "value": 1}, {"property": "a", "value": 2}]
    cps_b = list(reversed(cps_a))
    g1 = map_odcs_document(make(["z", "y"], auth_a, cps_a), namespace=NS)
    g2 = map_odcs_document(make(["y", "z"], auth_b, cps_b), namespace=NS)
    assert g1.content_identity() == g2.content_identity()


def test_custom_property_value_array_order_is_material() -> None:
    a = _minimal_contract(customProperties=[{"property": "flags", "value": ["x", "y"]}])
    b = _minimal_contract(customProperties=[{"property": "flags", "value": ["y", "x"]}])
    assert (
        map_odcs_document(a, namespace=NS).content_identity()
        != map_odcs_document(b, namespace=NS).content_identity()
    )


def test_object_key_order_irrelevant() -> None:
    # JSON loads with different key insertion order
    raw_a = (
        '{"status":"active","version":"1.0.0","kind":"DataContract",'
        '"apiVersion":"v3.1.0","id":"contract-orders","name":"Orders Contract"}'
    )
    raw_b = (
        '{"apiVersion":"v3.1.0","id":"contract-orders","kind":"DataContract",'
        '"name":"Orders Contract","status":"active","version":"1.0.0"}'
    )
    assert (
        map_odcs_document(json.loads(raw_a), namespace=NS).content_identity()
        == map_odcs_document(json.loads(raw_b), namespace=NS).content_identity()
    )


# --- H: Scope guards ---


def test_relationships_quality_servers_do_not_create_graph_semantics() -> None:
    graph = map_odcs_document(_rich_contract(), namespace=NS)
    assert not any(e.kind == EDGE_KIND_DEPENDS_ON for e in graph.edges)
    assert not any(n.identity.kind == NODE_KIND_DATA_SOURCE for n in graph.nodes)
    assert not any("quality" in n.to_dict()["attributes"] for n in graph.nodes)
    assert all(n.identity.namespace == NS for n in graph.nodes)


# --- I: Errors / conflicts / namespace ---


def test_duplicate_schema_same_id_rejected() -> None:
    doc = _minimal_contract(
        schema=[
            {"id": "same", "name": "orders", "logicalType": "object"},
            {"id": "same", "name": "orders", "logicalType": "object"},
        ]
    )
    with pytest.raises(OdcsMappingError) as exc:
        map_odcs_document(doc, namespace=NS)
    assert exc.value.errors[0].code == "odcs_mapping_error"


def test_duplicate_schema_fallback_name_rejected() -> None:
    doc = _minimal_contract(
        schema=[
            {"name": "orders", "logicalType": "object"},
            {"name": "orders", "logicalType": "object"},
        ]
    )
    with pytest.raises(OdcsMappingError):
        map_odcs_document(doc, namespace=NS)


def test_duplicate_property_same_id_rejected() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {"id": "x", "name": "a", "logicalType": "string"},
                    {"id": "x", "name": "b", "logicalType": "string"},
                ],
            }
        ]
    )
    with pytest.raises(OdcsMappingError):
        map_odcs_document(doc, namespace=NS)


def test_duplicate_property_fallback_name_rejected() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {"name": "a", "logicalType": "string"},
                    {"name": "a", "logicalType": "string"},
                ],
            }
        ]
    )
    with pytest.raises(OdcsMappingError):
        map_odcs_document(doc, namespace=NS)


def test_same_local_id_under_different_parents_ok() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [{"id": "shared", "name": "id", "logicalType": "string"}],
            },
            {
                "name": "customers",
                "properties": [{"id": "shared", "name": "id", "logicalType": "string"}],
            },
        ]
    )
    graph = map_odcs_document(doc, namespace=NS)
    shared = [n for n in graph.nodes if n.identity.logical_id == "shared"]
    assert len(shared) == 2


def test_distinct_ids_same_display_name_ok() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {"id": "p1", "name": "code", "logicalType": "string"},
                    {"id": "p2", "name": "code", "logicalType": "integer"},
                ],
            }
        ]
    )
    graph = map_odcs_document(doc, namespace=NS)
    codes = [n for n in graph.nodes if n.name == "code"]
    assert len(codes) == 2


def test_namespace_empty_raises_odcs_mapping_error() -> None:
    with pytest.raises(OdcsMappingError) as exc:
        map_odcs_document(_minimal_contract(), namespace="  ")
    assert exc.value.errors[0].path == ""
    assert exc.value.errors[0].message == "namespace is required"


def test_namespace_non_str_raises_odcs_mapping_error() -> None:
    with pytest.raises(OdcsMappingError) as exc:
        map_odcs_document(_minimal_contract(), namespace=123)  # type: ignore[arg-type]
    assert exc.value.errors[0].code == "odcs_mapping_error"


def test_load_odcs_graph_namespace_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "c.yaml", _minimal_contract())
    with pytest.raises(OdcsMappingError):
        load_odcs_graph(path, namespace="")


def test_empty_contract_id_raises_mapping_error() -> None:
    for value in ("", "   "):
        with pytest.raises(OdcsMappingError) as exc:
            map_odcs_document(_minimal_contract(id=value), namespace=NS)
        assert exc.value.errors[0].path == "/id"
        assert not isinstance(exc.value, ValueError)


def test_empty_contract_version_raises_mapping_error() -> None:
    for value in ("", "   "):
        with pytest.raises(OdcsMappingError) as exc:
            map_odcs_document(_minimal_contract(version=value), namespace=NS)
        assert exc.value.errors[0].path == "/version"


def test_empty_dataset_name_rejected_not_dropped() -> None:
    doc = _minimal_contract(schema=[{"name": "  ", "logicalType": "object"}])
    with pytest.raises(OdcsMappingError) as exc:
        map_odcs_document(doc, namespace=NS)
    assert exc.value.errors[0].path == "/schema/0/name"


def test_empty_property_name_rejected_not_dropped() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [{"name": "", "logicalType": "string"}],
            }
        ]
    )
    with pytest.raises(OdcsMappingError) as exc:
        map_odcs_document(doc, namespace=NS)
    assert exc.value.errors[0].path == "/schema/0/properties/0/name"


def test_nested_empty_property_name_exact_pointer() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {
                        "name": "customer",
                        "logicalType": "object",
                        "properties": [{"name": "  ", "logicalType": "string"}],
                    }
                ],
            }
        ]
    )
    with pytest.raises(OdcsMappingError) as exc:
        map_odcs_document(doc, namespace=NS)
    assert exc.value.errors[0].path == "/schema/0/properties/0/properties/0/name"


def test_id_collides_with_sibling_fallback_name() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {"id": "x", "name": "alpha", "logicalType": "string"},
                    {"name": "x", "logicalType": "string"},
                ],
            }
        ]
    )
    with pytest.raises(OdcsMappingError) as exc:
        map_odcs_document(doc, namespace=NS)
    assert exc.value.errors[0].code == "odcs_mapping_error"


def test_array_object_named_property_mapped() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {
                        "name": "items",
                        "logicalType": "array",
                        "items": {
                            "logicalType": "object",
                            "properties": [{"name": "sku", "logicalType": "string"}],
                        },
                    }
                ],
            }
        ]
    )
    graph = map_odcs_document(doc, namespace=NS)
    assert any(n.identity.logical_id == "sku" for n in graph.nodes)
    assert not any(n.identity.logical_id == "items_items" for n in graph.nodes)
    assert not any(n.name == "items" and n.identity.kind != NODE_KIND_COLUMN for n in graph.nodes)


def test_array_array_object_named_property_mapped() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {
                        "name": "batches",
                        "logicalType": "array",
                        "items": {
                            "logicalType": "array",
                            "items": {
                                "logicalType": "object",
                                "properties": [{"name": "sku", "logicalType": "string"}],
                            },
                        },
                    }
                ],
            }
        ]
    )
    graph = map_odcs_document(doc, namespace=NS)
    sku = next(n for n in graph.nodes if n.identity.logical_id == "sku")
    assert sku.identity.parent is not None
    assert sku.identity.parent.logical_id == "batches"
    assert not any(n.identity.logical_id == "items" for n in graph.nodes)


def test_three_level_array_before_object_maps_named_property() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {
                        "name": "cube",
                        "logicalType": "array",
                        "items": {
                            "logicalType": "array",
                            "items": {
                                "logicalType": "array",
                                "items": {
                                    "logicalType": "object",
                                    "properties": [{"name": "sku", "logicalType": "string"}],
                                },
                            },
                        },
                    }
                ],
            }
        ]
    )
    graph = map_odcs_document(doc, namespace=NS)
    assert any(n.identity.logical_id == "sku" for n in graph.nodes)


def test_duplicate_named_property_in_deep_items_rejected() -> None:
    doc = _minimal_contract(
        schema=[
            {
                "name": "orders",
                "properties": [
                    {
                        "name": "batches",
                        "logicalType": "array",
                        "items": {
                            "logicalType": "array",
                            "items": {
                                "logicalType": "object",
                                "properties": [
                                    {"name": "sku", "logicalType": "string"},
                                    {"name": "sku", "logicalType": "string"},
                                ],
                            },
                        },
                    }
                ],
            }
        ]
    )
    with pytest.raises(OdcsMappingError):
        map_odcs_document(doc, namespace=NS)


def test_deep_items_property_order_permutation_same_hash() -> None:
    def make(props: list[dict[str, str]]) -> dict[str, Any]:
        return _minimal_contract(
            schema=[
                {
                    "name": "orders",
                    "properties": [
                        {
                            "name": "batches",
                            "logicalType": "array",
                            "items": {
                                "logicalType": "array",
                                "items": {
                                    "logicalType": "object",
                                    "properties": props,
                                },
                            },
                        }
                    ],
                }
            ]
        )

    a = [
        {"name": "sku", "logicalType": "string"},
        {"name": "qty", "logicalType": "integer"},
    ]
    b = list(reversed(a))
    assert (
        map_odcs_document(make(a), namespace=NS).content_identity()
        == map_odcs_document(make(b), namespace=NS).content_identity()
    )


def test_public_api_surface_is_small() -> None:
    import governance.integrations.odcs as odcs

    assert "ODCS_SCHEMA_SHA256" not in odcs.__all__
    assert "load_odcs_schema" not in odcs.__all__
    assert "SUPPORTED_API_VERSION" not in odcs.__all__
    for name in (
        "load_odcs_document",
        "validate_odcs_document",
        "map_odcs_document",
        "load_odcs_graph",
    ):
        assert name in odcs.__all__


# --- J: Packaging / integrity ---


def test_schema_and_attribution_packaged() -> None:
    root = files("governance.integrations.odcs.schemas")
    schema_text = root.joinpath("odcs-json-schema-v3.1.0.json").read_text(encoding="utf-8")
    assert "Open Data Contract Standard" in schema_text
    license_text = root.joinpath("LICENSE-Apache-2.0.txt").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    notice = root.joinpath("ODCS-SCHEMA-NOTICE.txt").read_text(encoding="utf-8")
    assert "v3.1.0" in notice
    assert "Apache License 2.0" in notice


def test_pinned_schema_sha256_matches_upstream_artifact() -> None:
    raw = (
        files("governance.integrations.odcs.schemas")
        .joinpath("odcs-json-schema-v3.1.0.json")
        .read_bytes()
    )
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == ODCS_SCHEMA_SHA256
    assert digest == "2cb7dd6fe43344d2233e0406438622681dc3ebadcf8f0d606a15b40c8f6752c0"


def test_package_version_is_120() -> None:
    assert __version__ == "1.2.0"
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert 'version = "1.2.0"' in pyproject.read_text(encoding="utf-8")


def test_existing_four_schemas_still_packaged() -> None:
    assert (
        files("governance.config_contract.schemas")
        .joinpath("governance-config.v1.schema.json")
        .is_file()
    )
    assert files("governance.policy.schemas").joinpath("governance-policy.v1.schema.json").is_file()
    assert files("governance.plans.schemas").joinpath("governance-plan.v1.schema.json").is_file()
    assert (
        files("governance.github_ci.schemas")
        .joinpath("governance-action-result.v1.schema.json")
        .is_file()
    )


def test_third_party_notices_present() -> None:
    notices = Path(__file__).resolve().parents[1] / "THIRD_PARTY_NOTICES.md"
    text = notices.read_text(encoding="utf-8")
    assert "Open Data Contract Standard" in text
    assert "ODCS-SCHEMA-NOTICE.txt" in text
