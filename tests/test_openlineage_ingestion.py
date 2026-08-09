"""Tests for OpenLineage core 2-0-2 event loading, validation, and mapping."""

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
    GraphNodeIdentity,
)
from governance.identity.canonicalize import canonical_json_bytes
from governance.integrations.openlineage import (
    CODE_MAPPING,
    CODE_PARSE,
    CODE_READ,
    CODE_UNSUPPORTED_SCHEMA,
    CODE_VALIDATION,
    OpenLineageMappingError,
    OpenLineageParseError,
    OpenLineageReadError,
    OpenLineageUnsupportedSchemaError,
    OpenLineageValidationError,
    load_openlineage_events,
    load_openlineage_graph,
    map_openlineage_events,
    validate_openlineage_events,
)

NS = "acme.lineage"
PRODUCER = "https://example.com/producer/1.0"
RUN_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
JOB_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/JobEvent"
DATASET_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/DatasetEvent"
RUN_ID = "11111111-1111-1111-1111-111111111111"
EVENT_TIME = "2024-01-01T00:00:00Z"


def _job(namespace: str = "ns", name: str = "job") -> dict[str, Any]:
    return {"namespace": namespace, "name": name}


def _dataset(
    namespace: str = "postgres://host",
    name: str = "db.schema.table",
    *,
    facets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ds: dict[str, Any] = {"namespace": namespace, "name": name}
    if facets is not None:
        ds["facets"] = facets
    return ds


def _facet_base(
    *,
    producer: str = PRODUCER,
    schema_url: str = "https://openlineage.io/spec/facets/1-0-0/Example.json",
) -> dict[str, Any]:
    return {"_producer": producer, "_schemaURL": schema_url}


def _run_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "eventTime": EVENT_TIME,
        "producer": PRODUCER,
        "schemaURL": RUN_URL,
        "run": {"runId": RUN_ID},
        "job": _job(),
    }
    event.update(overrides)
    return event


def _job_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "eventTime": EVENT_TIME,
        "producer": PRODUCER,
        "schemaURL": JOB_URL,
        "job": _job(),
    }
    event.update(overrides)
    return event


def _dataset_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "eventTime": EVENT_TIME,
        "producer": PRODUCER,
        "schemaURL": DATASET_URL,
        "dataset": _dataset(),
    }
    event.update(overrides)
    return event


def _write_json(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- A: loader / JSON tree ---


def test_load_single_object(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "event.json", _run_event())
    loaded = load_openlineage_events(path)
    assert len(loaded) == 1
    assert loaded[0]["schemaURL"] == RUN_URL


def test_load_array_batch(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "batch.json", [_run_event(), _job_event()])
    loaded = load_openlineage_events(path)
    assert len(loaded) == 2


def test_load_empty_batch(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "empty.json", [])
    assert load_openlineage_events(path) == ()


def test_unsupported_suffix(tmp_path: Path) -> None:
    path = tmp_path / "events.yaml"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(OpenLineageReadError) as exc:
        load_openlineage_events(path)
    assert exc.value.errors[0].code == CODE_READ


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OpenLineageReadError) as exc:
        load_openlineage_events(tmp_path / "missing.json")
    assert exc.value.errors[0].code == CODE_READ


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(OpenLineageParseError) as exc:
        load_openlineage_events(path)
    assert exc.value.errors[0].code == CODE_PARSE


def test_nan_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"eventTime": NaN}', encoding="utf-8")
    with pytest.raises(OpenLineageParseError):
        load_openlineage_events(path)


def test_infinity_rejected(tmp_path: Path) -> None:
    path = tmp_path / "inf.json"
    path.write_text('{"eventTime": Infinity}', encoding="utf-8")
    with pytest.raises(OpenLineageParseError):
        load_openlineage_events(path)


def test_neg_infinity_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ninf.json"
    path.write_text('{"eventTime": -Infinity}', encoding="utf-8")
    with pytest.raises(OpenLineageParseError):
        load_openlineage_events(path)


def test_invalid_root_rejected(tmp_path: Path) -> None:
    path = tmp_path / "root.json"
    path.write_text('"string-root"', encoding="utf-8")
    with pytest.raises(OpenLineageParseError) as exc:
        load_openlineage_events(path)
    assert "mapping or array" in exc.value.errors[0].message


def test_array_item_non_mapping_rejected(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "items.json", [1])
    with pytest.raises(OpenLineageParseError) as exc:
        load_openlineage_events(path)
    assert exc.value.errors[0].path == "/0"


def test_direct_mapping_cycle_rejected() -> None:
    event: dict[str, Any] = {}
    event["self"] = event
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert "finite JSON tree" in exc.value.errors[0].message


def test_max_depth_rejected() -> None:
    nested: Any = {}
    current = nested
    for _ in range(70):
        nxt: dict[str, Any] = {}
        current["child"] = nxt
        current = nxt
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([nested])
    assert "finite JSON tree" in exc.value.errors[0].message


def test_non_string_key_rejected() -> None:
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([{1: "bad"}])  # type: ignore[dict-item]
    assert "finite JSON tree" in exc.value.errors[0].message


def test_unsupported_object_rejected() -> None:
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([{"x": object()}])  # type: ignore[list-item]
    assert "finite JSON tree" in exc.value.errors[0].message


# --- B: core schemaURL gate ---


def test_exact_run_job_dataset_202_accepted() -> None:
    validated = validate_openlineage_events([_run_event(), _job_event(), _dataset_event()])
    assert len(validated) == 3
    assert validated[0]["schemaURL"] == RUN_URL
    assert validated[1]["schemaURL"] == JOB_URL
    assert validated[2]["schemaURL"] == DATASET_URL


def test_105_run_event_rejected() -> None:
    event = _run_event(
        schemaURL="https://openlineage.io/spec/1-0-5/OpenLineage.json#/definitions/RunEvent"
    )
    with pytest.raises(OpenLineageUnsupportedSchemaError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].code == CODE_UNSUPPORTED_SCHEMA
    assert exc.value.errors[0].path == "/0/schemaURL"


def test_201_rejected() -> None:
    event = _run_event(
        schemaURL="https://openlineage.io/spec/2-0-1/OpenLineage.json#/$defs/RunEvent"
    )
    with pytest.raises(OpenLineageUnsupportedSchemaError):
        validate_openlineage_events([event])


def test_fake_203_rejected() -> None:
    event = _run_event(
        schemaURL="https://openlineage.io/spec/2-0-3/OpenLineage.json#/$defs/RunEvent"
    )
    with pytest.raises(OpenLineageUnsupportedSchemaError):
        validate_openlineage_events([event])


def test_raw_main_rejected() -> None:
    event = _run_event(
        schemaURL="https://raw.githubusercontent.com/OpenLineage/OpenLineage/main/spec/OpenLineage.json#/$defs/RunEvent"
    )
    with pytest.raises(OpenLineageUnsupportedSchemaError):
        validate_openlineage_events([event])


def test_definitions_alias_rejected() -> None:
    event = _run_event(
        schemaURL="https://openlineage.io/spec/2-0-2/OpenLineage.json#/definitions/RunEvent"
    )
    with pytest.raises(OpenLineageUnsupportedSchemaError):
        validate_openlineage_events([event])


def test_missing_schema_url_rejected() -> None:
    event = _run_event()
    del event["schemaURL"]
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/schemaURL"
    assert exc.value.errors[0].code == CODE_VALIDATION


def test_wrong_schema_url_rejected() -> None:
    event = _run_event(schemaURL="https://example.com/not-openlineage")
    with pytest.raises(OpenLineageUnsupportedSchemaError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/schemaURL"


# --- C: event contract ---


def test_producer_uri_required() -> None:
    event = _run_event(producer="not-a-uri")
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/producer"


def test_timezone_aware_event_time_required() -> None:
    event = _run_event(eventTime="2024-01-01T00:00:00")
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/eventTime"


def test_run_event_without_event_type_accepted() -> None:
    event = _run_event()
    assert "eventType" not in event
    validated = validate_openlineage_events([event])
    assert "eventType" not in validated[0]


def test_invalid_event_type_rejected() -> None:
    event = _run_event(eventType="DONE")
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/eventType"
    assert exc.value.errors[0].code == CODE_VALIDATION


def test_valid_event_type_accepted() -> None:
    event = _run_event(eventType="COMPLETE")
    validated = validate_openlineage_events([event])
    assert validated[0]["eventType"] == "COMPLETE"


def test_run_id_must_be_uuid() -> None:
    event = _run_event(run={"runId": "not-a-uuid"})
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/run/runId"


def test_job_and_dataset_required_fields() -> None:
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([_job_event(job={"namespace": "ns"})])
    assert exc.value.errors[0].path == "/0/job/name"

    with pytest.raises(OpenLineageValidationError) as exc2:
        validate_openlineage_events([_dataset_event(dataset={"namespace": "ns"})])
    assert exc2.value.errors[0].path == "/0/dataset/name"


def test_inputs_outputs_arrays_validated() -> None:
    event = _run_event(inputs=[_dataset()], outputs=[_dataset(name="out")])
    validated = validate_openlineage_events([event])
    assert len(validated[0]["inputs"]) == 1
    assert len(validated[0]["outputs"]) == 1


def test_job_event_with_run_forbidden() -> None:
    event = _job_event(run={"runId": RUN_ID})
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/run"
    assert "must not include run" in exc.value.errors[0].message


def test_dataset_event_with_run_forbidden() -> None:
    event = _dataset_event(run={"runId": RUN_ID})
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/run"


def test_dataset_event_with_job_forbidden() -> None:
    event = _dataset_event(job=_job())
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/job"


def test_supported_facet_shapes_basic() -> None:
    facets = {
        "schema": {**_facet_base(), "fields": [{"name": "id", "type": "int"}]},
        "storage": {**_facet_base(), "storageLayer": "iceberg", "fileFormat": "parquet"},
        "datasetType": {**_facet_base(), "datasetType": "TABLE", "subType": ""},
        "ownership": {
            **_facet_base(),
            "owners": [{"name": "alice", "type": "USER"}],
        },
        "hierarchy": {
            **_facet_base(),
            "hierarchy": [
                {"type": "DATABASE", "name": "db"},
                {"type": "SCHEMA", "name": "sch"},
                {"type": "TABLE", "name": "tbl"},
            ],
        },
    }
    event = _dataset_event(dataset=_dataset(facets=facets))
    validate_openlineage_events([event])


def test_storage_without_storage_layer_rejected() -> None:
    facets = {"storage": _facet_base()}
    event = _dataset_event(dataset=_dataset(facets=facets))
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/dataset/facets/storage/storageLayer"


def test_schema_without_fields_accepted() -> None:
    facets = {"schema": _facet_base()}
    event = _dataset_event(dataset=_dataset(facets=facets))
    validate_openlineage_events([event])


def test_deleted_supported_facet_rejected() -> None:
    facets = {
        "schema": {**_facet_base(), "_deleted": True, "fields": []},
    }
    event = _dataset_event(dataset=_dataset(facets=facets))
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([event])
    assert exc.value.errors[0].path == "/0/dataset/facets/schema/_deleted"


def test_schema_field_type_null_rejected() -> None:
    facets = {
        "schema": {
            **_facet_base(),
            "fields": [{"name": "id", "type": None}],
        }
    }
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))])
    assert exc.value.errors[0].path == "/0/dataset/facets/schema/fields/0/type"
    assert exc.value.errors[0].code == CODE_VALIDATION


def test_schema_field_description_null_rejected() -> None:
    facets = {
        "schema": {
            **_facet_base(),
            "fields": [{"name": "id", "description": None}],
        }
    }
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))])
    assert exc.value.errors[0].path == "/0/dataset/facets/schema/fields/0/description"


def test_dataset_type_subtype_null_rejected() -> None:
    facets = {
        "datasetType": {
            **_facet_base(),
            "datasetType": "TABLE",
            "subType": None,
        }
    }
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))])
    assert exc.value.errors[0].path == "/0/dataset/facets/datasetType/subType"


def test_storage_file_format_null_rejected() -> None:
    facets = {
        "storage": {
            **_facet_base(),
            "storageLayer": "iceberg",
            "fileFormat": None,
        }
    }
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))])
    assert exc.value.errors[0].path == "/0/dataset/facets/storage/fileFormat"


def test_optional_string_fields_absent_still_valid() -> None:
    facets = {
        "schema": {
            **_facet_base(),
            "fields": [{"name": "id"}],
        },
        "datasetType": {**_facet_base(), "datasetType": "TABLE"},
        "storage": {**_facet_base(), "storageLayer": "iceberg"},
    }
    validate_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))])


def _nodes_by_kind(graph: Any, kind: str) -> list[Any]:
    return [node for node in graph.nodes if node.identity.kind == kind]


def _edges_by_kind(graph: Any, kind: str) -> list[Any]:
    return [edge for edge in graph.edges if edge.kind == kind]


def _physical_hierarchy_facet(
    database: str = "Db",
    schema: str = "Sch",
    table: str = "Tbl",
) -> dict[str, Any]:
    return {
        **_facet_base(
            schema_url="https://openlineage.io/spec/facets/1-0-0/HierarchyDatasetFacet.json"
        ),
        "hierarchy": [
            {"type": "DATABASE", "name": database},
            {"type": "SCHEMA", "name": schema},
            {"type": "TABLE", "name": table},
        ],
    }


# --- C continued: eventType does not affect graph ---


def test_event_type_differences_do_not_change_graph() -> None:
    base_inputs = [_dataset(name="in")]
    base_outputs = [_dataset(name="out")]
    a = _run_event(eventType="START", inputs=base_inputs, outputs=base_outputs)
    b = _run_event(eventType="COMPLETE", inputs=base_inputs, outputs=base_outputs)
    g1 = map_openlineage_events([a], namespace=NS)
    g2 = map_openlineage_events([b], namespace=NS)
    assert g1.content_identity() == g2.content_identity()
    assert g1.to_dict() == g2.to_dict()


# --- D: caller namespace ---


def test_namespace_required() -> None:
    with pytest.raises(OpenLineageMappingError) as exc:
        map_openlineage_events([_dataset_event()], namespace="  ")
    assert exc.value.errors[0].code == CODE_MAPPING
    assert "namespace" in exc.value.errors[0].message


def test_namespace_trimmed_and_not_provider() -> None:
    graph = map_openlineage_events([_dataset_event()], namespace="  acme.lineage  ")
    assert all(node.identity.namespace == NS for node in graph.nodes)
    assert not any(node.identity.namespace == "openlineage" for node in graph.nodes)


def test_empty_batch_maps_to_empty_graph(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "empty.json", [])
    graph = load_openlineage_graph(path, namespace=NS)
    assert graph.nodes == ()
    assert graph.edges == ()


# --- E: generic dataset identity ---


def test_generic_dataset_identity_canonical_json() -> None:
    ds = _dataset(namespace="ol-ns", name="ol-name")
    graph = map_openlineage_events([_dataset_event(dataset=ds)], namespace=NS)
    datasets = _nodes_by_kind(graph, NODE_KIND_DATASET)
    assert len(datasets) == 1
    expected = canonical_json_bytes(["ol-ns", "ol-name"]).decode("utf-8")
    assert datasets[0].identity.logical_id == expected
    assert datasets[0].identity.parent is None
    assert datasets[0].name == "ol-name"


def test_generic_identity_case_preserved() -> None:
    ds = _dataset(namespace="Ol.Ns", name="Table.Name")
    graph = map_openlineage_events([_dataset_event(dataset=ds)], namespace=NS)
    node = _nodes_by_kind(graph, NODE_KIND_DATASET)[0]
    assert node.identity.logical_id == canonical_json_bytes(["Ol.Ns", "Table.Name"]).decode("utf-8")


# --- F: physical hierarchy ---


def test_exact_hierarchy_physical_identity() -> None:
    ds = _dataset(
        namespace="postgres://host",
        name="ignored.for.identity",
        facets={"hierarchy": _physical_hierarchy_facet("Db", "Sch", "Tbl")},
    )
    graph = map_openlineage_events([_dataset_event(dataset=ds)], namespace=NS)
    tables = _nodes_by_kind(graph, NODE_KIND_TABLE)
    assert len(tables) == 1
    table = tables[0].identity
    assert table == GraphNodeIdentity(
        NS,
        NODE_KIND_TABLE,
        "Tbl",
        parent=GraphNodeIdentity(
            NS,
            NODE_KIND_DATASET,
            "Sch",
            parent=GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, "Db"),
        ),
    )
    assert len(_nodes_by_kind(graph, NODE_KIND_DATA_SOURCE)) == 1
    assert len(_edges_by_kind(graph, EDGE_KIND_CONTAINS)) >= 2


def test_hierarchy_fallback_catalog() -> None:
    facets = {
        "hierarchy": {
            **_facet_base(),
            "hierarchy": [
                {"type": "CATALOG", "name": "c"},
                {"type": "SCHEMA", "name": "s"},
                {"type": "TABLE", "name": "t"},
            ],
        }
    }
    ds = _dataset(namespace="n", name="n.t", facets=facets)
    graph = map_openlineage_events([_dataset_event(dataset=ds)], namespace=NS)
    assert _nodes_by_kind(graph, NODE_KIND_TABLE) == []
    datasets = _nodes_by_kind(graph, NODE_KIND_DATASET)
    assert len(datasets) == 1
    assert datasets[0].to_dict()["attributes"]["hierarchy"][0]["type"] == "CATALOG"


def test_hierarchy_wrong_order_fallback() -> None:
    facets = {
        "hierarchy": {
            **_facet_base(),
            "hierarchy": [
                {"type": "TABLE", "name": "t"},
                {"type": "SCHEMA", "name": "s"},
                {"type": "DATABASE", "name": "d"},
            ],
        }
    }
    graph = map_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))], namespace=NS)
    assert _nodes_by_kind(graph, NODE_KIND_TABLE) == []


# --- G: schema columns ---


def test_schema_top_level_and_nested_columns() -> None:
    facets = {
        "schema": {
            **_facet_base(
                schema_url="https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json"
            ),
            "fields": [
                {"name": "id", "type": "int"},
                {
                    "name": "address",
                    "type": "struct",
                    "fields": [{"name": "city", "type": "string", "description": "City"}],
                },
            ],
        }
    }
    graph = map_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))], namespace=NS)
    columns = _nodes_by_kind(graph, NODE_KIND_COLUMN)
    names = {node.name for node in columns}
    assert names == {"id", "address", "city"}
    city = next(node for node in columns if node.name == "city")
    assert city.identity.parent is not None
    assert city.identity.parent.logical_id == "address"
    assert city.description == "City"
    assert city.to_dict()["attributes"]["data_type"] == "string"


def test_schema_field_order_irrelevant() -> None:
    f1 = {
        "schema": {
            **_facet_base(),
            "fields": [{"name": "a"}, {"name": "b"}],
        }
    }
    f2 = {
        "schema": {
            **_facet_base(),
            "fields": [{"name": "b"}, {"name": "a"}],
        }
    }
    g1 = map_openlineage_events([_dataset_event(dataset=_dataset(facets=f1))], namespace=NS)
    g2 = map_openlineage_events([_dataset_event(dataset=_dataset(facets=f2))], namespace=NS)
    assert g1.content_identity() == g2.content_identity()


def test_duplicate_sibling_field_name_rejected() -> None:
    facets = {
        "schema": {
            **_facet_base(),
            "fields": [{"name": "a"}, {"name": "a"}],
        }
    }
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))])
    assert "duplicate" in exc.value.errors[0].message


def test_column_lineage_does_not_create_edges() -> None:
    facets = {
        "columnLineage": {
            **_facet_base(),
            "fields": {
                "out_col": {
                    "inputFields": [
                        {
                            "namespace": "n",
                            "name": "in",
                            "field": "in_col",
                        }
                    ]
                }
            },
        }
    }
    inputs = [_dataset(name="in")]
    outputs = [_dataset(name="out", facets=facets)]
    graph = map_openlineage_events(
        [_run_event(inputs=inputs, outputs=outputs)],
        namespace=NS,
    )
    assert len(_edges_by_kind(graph, EDGE_KIND_DEPENDS_ON)) == 1
    assert all(
        edge.source.kind != NODE_KIND_COLUMN and edge.target.kind != NODE_KIND_COLUMN
        for edge in graph.edges
        if edge.kind == EDGE_KIND_DEPENDS_ON
    )


# --- H: datasetType / storage / ownership ---


def test_dataset_type_storage_ownership_and_facet_provenance() -> None:
    facet_producer = "https://facet.producer/1"
    facet_schema = "https://openlineage.io/spec/facets/1-0-0/OwnershipDatasetFacet.json"
    schema_facet_url = "https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json"
    facets = {
        "datasetType": {
            **_facet_base(producer=facet_producer),
            "datasetType": "VIEW",
            "subType": "MATERIALIZED",
        },
        "storage": {
            **_facet_base(),
            "storageLayer": "iceberg",
            "fileFormat": "parquet",
        },
        "ownership": {
            "_producer": facet_producer,
            "_schemaURL": facet_schema,
            "owners": [
                {"name": "bob", "type": "USER"},
                {"name": "alice", "type": "USER"},
                {"name": "alice", "type": "USER"},
            ],
        },
        "schema": {
            "_producer": facet_producer,
            "_schemaURL": schema_facet_url,
            "fields": [{"name": "id", "type": "int"}],
        },
    }
    graph = map_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))], namespace=NS)
    node = _nodes_by_kind(graph, NODE_KIND_DATASET)[0]
    attrs = node.to_dict()["attributes"]
    assert attrs["dataset_type"] == "VIEW"
    assert attrs["dataset_subtype"] == "MATERIALIZED"
    assert attrs["storage_layer"] == "iceberg"
    assert attrs["file_format"] == "parquet"
    assert attrs["ownership"] == [
        {"name": "alice", "type": "USER"},
        {"name": "bob", "type": "USER"},
    ]
    column = _nodes_by_kind(graph, NODE_KIND_COLUMN)[0]
    assert any(
        prov.source_version == schema_facet_url and prov.provider_type == "openlineage"
        for prov in column.provenance
    )
    assert facet_schema not in {prov.source_version for prov in column.provenance}


def test_schema_facet_without_fields_maps_no_columns() -> None:
    facets = {"schema": _facet_base()}
    graph = map_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))], namespace=NS)
    assert _nodes_by_kind(graph, NODE_KIND_COLUMN) == []


def test_dataset_type_without_dataset_type_rejected() -> None:
    facets = {"datasetType": _facet_base()}
    with pytest.raises(OpenLineageValidationError) as exc:
        validate_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))])
    assert exc.value.errors[0].path.endswith("/datasetType/datasetType")


def test_empty_subtype_omits_attr() -> None:
    facets = {
        "datasetType": {**_facet_base(), "datasetType": "TABLE", "subType": "  "},
    }
    graph = map_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))], namespace=NS)
    node = _nodes_by_kind(graph, NODE_KIND_DATASET)[0]
    attrs = node.to_dict()["attributes"]
    assert attrs["dataset_type"] == "TABLE"
    assert "dataset_subtype" not in attrs


# --- I: unsupported facets hash-invariant ---


def test_unsupported_facet_payload_does_not_affect_graph() -> None:
    base = _dataset(facets={"documentation": {**_facet_base(), "description": "a"}})
    other = _dataset(facets={"documentation": {**_facet_base(), "description": "b"}})
    g1 = map_openlineage_events([_dataset_event(dataset=base)], namespace=NS)
    g2 = map_openlineage_events([_dataset_event(dataset=other)], namespace=NS)
    assert g1.content_identity() == g2.content_identity()
    assert "documentation" not in g1.nodes[0].to_dict()["attributes"]


# --- J already covered in validation; mapping path ---


def test_deleted_supported_facet_fails_before_mapping() -> None:
    facets = {"ownership": {**_facet_base(), "_deleted": True, "owners": []}}
    with pytest.raises(OpenLineageValidationError):
        map_openlineage_events([_dataset_event(dataset=_dataset(facets=facets))], namespace=NS)


# --- K: provenance excludes runId/time ---


def test_provenance_excludes_run_id_and_event_time() -> None:
    graph = map_openlineage_events(
        [
            _run_event(
                eventTime="2024-06-01T12:00:00Z",
                run={"runId": RUN_ID},
                inputs=[_dataset(name="in")],
                outputs=[_dataset(name="out")],
            )
        ],
        namespace=NS,
    )
    payload = canonical_json_bytes(graph.to_dict()).decode("utf-8")
    assert RUN_ID not in payload
    assert "2024-06-01T12:00:00Z" not in payload
    assert "eventTime" not in payload
    assert "runId" not in payload


def test_producer_union_on_equivalent_observations() -> None:
    ds = _dataset(name="shared")
    e1 = _dataset_event(producer="https://a.example/p", dataset=ds)
    e2 = _dataset_event(producer="https://b.example/p", dataset=ds)
    graph = map_openlineage_events([e1, e2], namespace=NS)
    node = _nodes_by_kind(graph, NODE_KIND_DATASET)[0]
    producers = {prov.source_ref for prov in node.provenance}
    assert len(producers) == 2


def test_ownership_facet_provenance_preserved_with_event_producer() -> None:
    ownership_schema = "https://openlineage.io/spec/facets/1-0-0/OwnershipDatasetFacet.json"
    ownership_producer = "https://facet.producer/a"
    event_producer = "https://event.producer/b"
    facets = {
        "ownership": {
            "_producer": ownership_producer,
            "_schemaURL": ownership_schema,
            "owners": [{"name": "alice", "type": "USER"}],
        }
    }
    graph = map_openlineage_events(
        [
            _dataset_event(
                producer=event_producer,
                dataset=_dataset(namespace="ol-ns", name="owned", facets=facets),
            )
        ],
        namespace=NS,
    )
    node = _nodes_by_kind(graph, NODE_KIND_DATASET)[0]
    versions = {prov.source_version for prov in node.provenance}
    refs = {prov.source_ref for prov in node.provenance}
    assert DATASET_URL in versions
    assert ownership_schema in versions
    assert canonical_json_bytes([event_producer, "ol-ns", "owned"]).decode("utf-8") in refs
    assert (
        canonical_json_bytes([ownership_producer, "ol-ns", "owned", "ownership"]).decode("utf-8")
        in refs
    )


def test_supported_facet_provenance_union_order_independent() -> None:
    type_schema = "https://openlineage.io/spec/facets/1-0-0/DatasetTypeDatasetFacet.json"
    storage_schema = "https://openlineage.io/spec/facets/1-0-0/StorageDatasetFacet.json"
    ownership_schema = "https://openlineage.io/spec/facets/1-0-0/OwnershipDatasetFacet.json"
    e1 = _dataset_event(
        producer="https://event/one",
        dataset=_dataset(
            name="t",
            facets={
                "datasetType": {
                    "_producer": "https://facet/type",
                    "_schemaURL": type_schema,
                    "datasetType": "TABLE",
                },
                "storage": {
                    "_producer": "https://facet/storage",
                    "_schemaURL": storage_schema,
                    "storageLayer": "iceberg",
                },
            },
        ),
    )
    e2 = _dataset_event(
        producer="https://event/two",
        dataset=_dataset(
            name="t",
            facets={
                "ownership": {
                    "_producer": "https://facet/ownership",
                    "_schemaURL": ownership_schema,
                    "owners": [{"name": "alice", "type": "USER"}],
                }
            },
        ),
    )
    g1 = map_openlineage_events([e1, e2], namespace=NS)
    g2 = map_openlineage_events([e2, e1], namespace=NS)
    assert g1.content_identity() == g2.content_identity()
    assert g1.to_dict() == g2.to_dict()
    versions = {prov.source_version for prov in _nodes_by_kind(g1, NODE_KIND_DATASET)[0].provenance}
    assert type_schema in versions
    assert storage_schema in versions
    assert ownership_schema in versions
    assert DATASET_URL in versions


def test_physical_hierarchy_facet_provenance_on_table_not_containers() -> None:
    hierarchy_schema = "https://openlineage.io/spec/facets/1-0-0/HierarchyDatasetFacet.json"
    hierarchy_producer = "https://facet/hierarchy"
    facets = {
        "hierarchy": {
            "_producer": hierarchy_producer,
            "_schemaURL": hierarchy_schema,
            "hierarchy": [
                {"type": "DATABASE", "name": "Db"},
                {"type": "SCHEMA", "name": "Sch"},
                {"type": "TABLE", "name": "Tbl"},
            ],
        }
    }
    graph = map_openlineage_events(
        [_dataset_event(dataset=_dataset(facets=facets))],
        namespace=NS,
    )
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0]
    assert any(prov.source_version == hierarchy_schema for prov in table.provenance)
    assert any(hierarchy_producer in prov.source_ref for prov in table.provenance)
    for node in _nodes_by_kind(graph, NODE_KIND_DATA_SOURCE):
        assert node.provenance == ()
    for node in _nodes_by_kind(graph, NODE_KIND_DATASET):
        assert node.provenance == ()


def test_empty_schema_facet_provenance_preserved_without_columns() -> None:
    schema_url = "https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json"
    schema_producer = "https://facet/schema"
    facets = {
        "schema": {
            "_producer": schema_producer,
            "_schemaURL": schema_url,
        }
    }
    graph = map_openlineage_events(
        [_dataset_event(dataset=_dataset(facets=facets))],
        namespace=NS,
    )
    assert _nodes_by_kind(graph, NODE_KIND_COLUMN) == []
    node = _nodes_by_kind(graph, NODE_KIND_DATASET)[0]
    assert any(prov.source_version == schema_url for prov in node.provenance)
    assert any(schema_producer in prov.source_ref for prov in node.provenance)


def test_unsupported_facet_provenance_excluded_from_graph() -> None:
    custom_schema = "https://example.com/custom-facet.json"
    custom_producer = "https://custom.producer/1"
    base = _dataset(
        facets={
            "ownership": {
                **_facet_base(),
                "owners": [{"name": "alice", "type": "USER"}],
            },
            "customStuff": {
                "_producer": custom_producer,
                "_schemaURL": custom_schema,
                "value": "a",
            },
        }
    )
    other = _dataset(
        facets={
            "ownership": {
                **_facet_base(),
                "owners": [{"name": "alice", "type": "USER"}],
            },
            "customStuff": {
                "_producer": "https://other.custom/2",
                "_schemaURL": "https://example.com/other-custom.json",
                "value": "b",
            },
        }
    )
    g1 = map_openlineage_events([_dataset_event(dataset=base)], namespace=NS)
    g2 = map_openlineage_events([_dataset_event(dataset=other)], namespace=NS)
    assert g1.content_identity() == g2.content_identity()
    assert g1.to_dict() == g2.to_dict()
    payload = canonical_json_bytes(g1.to_dict()).decode("utf-8")
    assert custom_schema not in payload
    assert custom_producer not in payload


# --- L: lineage ---


def test_lineage_cross_product_direction_dedup_self_edge() -> None:
    inputs = [_dataset(name="a"), _dataset(name="b"), _dataset(name="a")]
    outputs = [_dataset(name="out"), _dataset(name="out")]
    graph = map_openlineage_events(
        [_run_event(inputs=inputs, outputs=outputs)],
        namespace=NS,
    )
    deps = _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON)
    assert len(deps) == 2
    targets = {edge.target.logical_id for edge in deps}
    assert len(targets) == 2

    self_graph = map_openlineage_events(
        [_run_event(inputs=[_dataset(name="x")], outputs=[_dataset(name="x")])],
        namespace=NS,
    )
    self_deps = _edges_by_kind(self_graph, EDGE_KIND_DEPENDS_ON)
    assert len(self_deps) == 1
    assert self_deps[0].source == self_deps[0].target


def test_dataset_event_has_no_lineage_edges() -> None:
    graph = map_openlineage_events([_dataset_event()], namespace=NS)
    assert _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON) == []


def test_job_event_per_event_lineage() -> None:
    graph = map_openlineage_events(
        [
            _job_event(
                inputs=[_dataset(name="in")],
                outputs=[_dataset(name="out")],
            )
        ],
        namespace=NS,
    )
    deps = _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON)
    assert len(deps) == 1
    assert deps[0].kind == EDGE_KIND_DEPENDS_ON


# --- M: Run lifecycle aggregation ---


def test_start_inputs_complete_outputs_same_run_creates_edge() -> None:
    start = _run_event(
        eventType="START",
        inputs=[_dataset(name="A")],
        outputs=[],
    )
    complete = _run_event(
        eventType="COMPLETE",
        eventTime="2024-01-01T01:00:00Z",
        inputs=[],
        outputs=[_dataset(name="B")],
    )
    graph = map_openlineage_events([start, complete], namespace=NS)
    deps = _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON)
    assert len(deps) == 1
    out_id = canonical_json_bytes(["postgres://host", "B"]).decode("utf-8")
    in_id = canonical_json_bytes(["postgres://host", "A"]).decode("utf-8")
    assert deps[0].source.logical_id == out_id
    assert deps[0].target.logical_id == in_id


def test_lifecycle_order_and_run_id_replacement_invariant() -> None:
    start = _run_event(eventType="START", inputs=[_dataset(name="A")])
    complete = _run_event(
        eventType="COMPLETE",
        outputs=[_dataset(name="B")],
    )
    g1 = map_openlineage_events([start, complete], namespace=NS)
    g2 = map_openlineage_events([complete, start], namespace=NS)
    assert g1.content_identity() == g2.content_identity()
    assert g1.to_dict() == g2.to_dict()

    alt_id = "22222222-2222-2222-2222-222222222222"
    start2 = _run_event(eventType="START", run={"runId": alt_id}, inputs=[_dataset(name="A")])
    complete2 = _run_event(
        eventType="COMPLETE",
        run={"runId": alt_id},
        outputs=[_dataset(name="B")],
    )
    g3 = map_openlineage_events([start2, complete2], namespace=NS)
    assert g3.content_identity() == g1.content_identity()


def test_different_run_ids_do_not_correlate() -> None:
    start = _run_event(
        eventType="START",
        run={"runId": RUN_ID},
        inputs=[_dataset(name="A")],
    )
    complete = _run_event(
        eventType="COMPLETE",
        run={"runId": "22222222-2222-2222-2222-222222222222"},
        outputs=[_dataset(name="B")],
    )
    graph = map_openlineage_events([start, complete], namespace=NS)
    assert _edges_by_kind(graph, EDGE_KIND_DEPENDS_ON) == []


def test_same_run_id_different_job_mapping_error() -> None:
    e1 = _run_event(job=_job(namespace="ns", name="job1"), inputs=[_dataset(name="A")])
    e2 = _run_event(
        job=_job(namespace="ns", name="job2"),
        outputs=[_dataset(name="B")],
    )
    with pytest.raises(OpenLineageMappingError) as exc:
        map_openlineage_events([e1, e2], namespace=NS)
    assert exc.value.errors[0].code == CODE_MAPPING
    err1 = exc.value.errors
    with pytest.raises(OpenLineageMappingError) as exc2:
        map_openlineage_events([e2, e1], namespace=NS)
    assert exc2.value.errors == err1


def test_duplicate_lifecycle_events_dedup() -> None:
    start = _run_event(eventType="START", inputs=[_dataset(name="A")])
    complete = _run_event(eventType="COMPLETE", outputs=[_dataset(name="B")])
    g1 = map_openlineage_events([start, complete], namespace=NS)
    g2 = map_openlineage_events([start, start, complete, complete], namespace=NS)
    assert g1.content_identity() == g2.content_identity()


# --- N: aggregation ---


def test_aggregation_missing_present_equivalent_conflict() -> None:
    ds_plain = _dataset(name="t")
    ds_owned = _dataset(
        name="t",
        facets={
            "ownership": {
                **_facet_base(),
                "owners": [{"name": "alice", "type": "USER"}],
            }
        },
    )
    g1 = map_openlineage_events(
        [_dataset_event(dataset=ds_plain), _dataset_event(dataset=ds_owned)],
        namespace=NS,
    )
    g2 = map_openlineage_events(
        [_dataset_event(dataset=ds_owned), _dataset_event(dataset=ds_plain)],
        namespace=NS,
    )
    assert g1.content_identity() == g2.content_identity()
    assert g1.nodes[0].to_dict()["attributes"]["ownership"] == [{"name": "alice", "type": "USER"}]

    conflict_a = _dataset(
        name="t",
        facets={
            "storage": {**_facet_base(), "storageLayer": "iceberg"},
        },
    )
    conflict_b = _dataset(
        name="t",
        facets={
            "storage": {**_facet_base(), "storageLayer": "delta"},
        },
    )
    with pytest.raises(OpenLineageMappingError):
        map_openlineage_events(
            [_dataset_event(dataset=conflict_a), _dataset_event(dataset=conflict_b)],
            namespace=NS,
        )
    with pytest.raises(OpenLineageMappingError) as exc:
        map_openlineage_events(
            [_dataset_event(dataset=conflict_b), _dataset_event(dataset=conflict_a)],
            namespace=NS,
        )
    with pytest.raises(OpenLineageMappingError) as exc2:
        map_openlineage_events(
            [_dataset_event(dataset=conflict_a), _dataset_event(dataset=conflict_b)],
            namespace=NS,
        )
    assert exc.value.errors == exc2.value.errors


# --- O: dbt-compatible physical identity equality ---


def test_physical_identity_matches_dbt_contract() -> None:
    ds = _dataset(facets={"hierarchy": _physical_hierarchy_facet("analytics", "marts", "orders")})
    graph = map_openlineage_events([_dataset_event(dataset=ds)], namespace=NS)
    table = _nodes_by_kind(graph, NODE_KIND_TABLE)[0].identity
    expected = GraphNodeIdentity(
        NS,
        NODE_KIND_TABLE,
        "orders",
        parent=GraphNodeIdentity(
            NS,
            NODE_KIND_DATASET,
            "marts",
            parent=GraphNodeIdentity(NS, NODE_KIND_DATA_SOURCE, "analytics"),
        ),
    )
    assert table == expected


# --- P: public API / package / scope ---


def test_public_api_surface_is_small() -> None:
    import governance.integrations.openlineage as ol

    assert "SUPPORTED_DATASET_FACETS" not in ol.__all__
    assert "_SUPPORTED_CORE_SCHEMA_URLS" not in ol.__all__
    assert "_copy_json_tree" not in ol.__all__
    for name in (
        "load_openlineage_events",
        "validate_openlineage_events",
        "map_openlineage_events",
        "load_openlineage_graph",
        "OpenLineageMappingError",
        "CODE_MAPPING",
        "CODE_UNSUPPORTED_SCHEMA",
    ):
        assert name in ol.__all__


def test_package_version_remains_110() -> None:
    assert __version__ == "1.1.0"
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert 'version = "1.1.0"' in pyproject.read_text(encoding="utf-8")


def test_no_openlineage_runtime_dependency_declared() -> None:
    text = (
        Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text(encoding="utf-8")
    )
    assert "openlineage-python" not in text
    assert "marquez" not in text.lower()


def test_load_graph_end_to_end(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "events.json",
        [
            _run_event(
                inputs=[_dataset(name="in")],
                outputs=[_dataset(name="out")],
            )
        ],
    )
    graph = load_openlineage_graph(path, namespace=NS)
    assert len(_edges_by_kind(graph, EDGE_KIND_DEPENDS_ON)) == 1
