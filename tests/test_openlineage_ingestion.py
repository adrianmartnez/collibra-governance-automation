"""Tests for OpenLineage core 2-0-2 event loading, validation, and mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from governance.integrations.openlineage import (
    CODE_PARSE,
    CODE_READ,
    CODE_UNSUPPORTED_SCHEMA,
    CODE_VALIDATION,
    OpenLineageParseError,
    OpenLineageReadError,
    OpenLineageUnsupportedSchemaError,
    OpenLineageValidationError,
    load_openlineage_events,
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
