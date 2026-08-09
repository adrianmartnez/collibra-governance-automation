"""Validate the consumed OpenLineage core 2-0-2 event subset (no schema vendoring).

Supported core event schemaURLs (exact match only):
https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/{RunEvent,JobEvent,DatasetEvent}
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from governance.integrations.openlineage.errors import (
    CODE_UNSUPPORTED_SCHEMA,
    CODE_VALIDATION,
    OpenLineageDiagnostic,
    OpenLineageUnsupportedSchemaError,
    OpenLineageValidationError,
)

_MAX_JSON_TREE_DEPTH = 64

_EVENT_KIND_RUN = "RunEvent"
_EVENT_KIND_JOB = "JobEvent"
_EVENT_KIND_DATASET = "DatasetEvent"

_SUPPORTED_CORE_SCHEMA_URLS: dict[str, str] = {
    "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent": _EVENT_KIND_RUN,
    "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/JobEvent": _EVENT_KIND_JOB,
    "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/DatasetEvent": (_EVENT_KIND_DATASET),
}

_EVENT_TYPES = frozenset({"START", "RUNNING", "COMPLETE", "ABORT", "FAIL", "OTHER"})

SUPPORTED_DATASET_FACETS = frozenset({"schema", "hierarchy", "datasetType", "storage", "ownership"})

_COLUMN_LINEAGE_SCHEMA_URL = (
    "https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json"
)


def _validation_error(path: str, message: str) -> OpenLineageValidationError:
    return OpenLineageValidationError(
        [
            OpenLineageDiagnostic(
                code=CODE_VALIDATION,
                path=path,
                message=message,
            )
        ]
    )


def _unsupported_schema_error(path: str) -> OpenLineageUnsupportedSchemaError:
    return OpenLineageUnsupportedSchemaError(
        [
            OpenLineageDiagnostic(
                code=CODE_UNSUPPORTED_SCHEMA,
                path=path,
                message="unsupported OpenLineage core schemaURL",
            )
        ]
    )


def _pointer(parent: str, *parts: str | int) -> str:
    result = parent
    for part in parts:
        text = str(part).replace("~", "~0").replace("/", "~1")
        result = f"{result}/{text}" if result else f"/{text}"
    return result


def _copy_json_tree(value: object, *, _depth: int = 0, _stack: frozenset[int] | None = None) -> Any:
    """Deep-copy a JSON-compatible tree; reject cycles, non-finite floats, excess depth."""
    if _stack is None:
        _stack = frozenset()
    if _depth > _MAX_JSON_TREE_DEPTH:
        raise _validation_error("", "document is not a finite JSON tree")

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _validation_error("", "document is not a finite JSON tree")
        return value

    if isinstance(value, Mapping):
        obj_id = id(value)
        if obj_id in _stack:
            raise _validation_error("", "document is not a finite JSON tree")
        nested_stack = _stack | {obj_id}
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _validation_error("", "document is not a finite JSON tree")
            result[key] = _copy_json_tree(item, _depth=_depth + 1, _stack=nested_stack)
        return result

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        arr_id = id(value)
        if arr_id in _stack:
            raise _validation_error("", "document is not a finite JSON tree")
        nested_stack = _stack | {arr_id}
        return [_copy_json_tree(item, _depth=_depth + 1, _stack=nested_stack) for item in value]

    raise _validation_error("", "document is not a finite JSON tree")


def _require_mapping(value: object, *, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _validation_error(path, "value must be a mapping")
    return dict(value)


def _require_non_empty_str(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _validation_error(path, "value must be a non-empty string")
    return value


def _require_list(value: object, *, path: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise _validation_error(path, "value must be an array")
    return list(value)


def _require_uri_string(value: object, *, path: str) -> str:
    text = _require_non_empty_str(value, path=path)
    parsed = urlparse(text.strip())
    if not parsed.scheme:
        raise _validation_error(path, "value must be a URI string")
    return text


def _require_event_time(value: object, *, path: str) -> str:
    text = _require_non_empty_str(value, path=path)
    candidate = text.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise _validation_error(path, "value must be a timezone-aware ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise _validation_error(path, "value must be a timezone-aware ISO-8601 datetime")
    return text


def _require_uuid(value: object, *, path: str) -> str:
    text = _require_non_empty_str(value, path=path)
    try:
        uuid.UUID(text.strip())
    except ValueError as exc:
        raise _validation_error(path, "value must be a valid UUID") from exc
    return text


def _validate_job(job: object, *, path: str) -> None:
    mapping = _require_mapping(job, path=path)
    _require_non_empty_str(mapping.get("namespace"), path=_pointer(path, "namespace"))
    _require_non_empty_str(mapping.get("name"), path=_pointer(path, "name"))
    if "facets" in mapping:
        facets = _require_mapping(mapping["facets"], path=_pointer(path, "facets"))
        for key, facet in facets.items():
            facet_path = _pointer(path, "facets", key)
            _require_mapping(facet, path=facet_path)
            # Job facets are out of scope; only ensure JSON mapping shape.


def _validate_schema_fields(fields: object, *, path: str) -> None:
    items = _require_list(fields, path=path)
    seen: set[str] = set()
    for index, field in enumerate(items):
        field_path = _pointer(path, index)
        mapping = _require_mapping(field, path=field_path)
        name = _require_non_empty_str(mapping.get("name"), path=_pointer(field_path, "name"))
        normalized = name.strip()
        if normalized in seen:
            raise _validation_error(
                _pointer(field_path, "name"),
                "duplicate sibling field name",
            )
        seen.add(normalized)
        if "type" in mapping and not isinstance(mapping["type"], str):
            raise _validation_error(
                _pointer(field_path, "type"),
                "value must be a string",
            )
        if "description" in mapping and not isinstance(mapping["description"], str):
            raise _validation_error(
                _pointer(field_path, "description"),
                "value must be a string",
            )
        if "fields" in mapping:
            _validate_schema_fields(mapping["fields"], path=_pointer(field_path, "fields"))


def _validate_supported_facet(key: str, facet: Mapping[str, Any], *, path: str) -> None:
    _require_uri_string(facet.get("_producer"), path=_pointer(path, "_producer"))
    _require_uri_string(facet.get("_schemaURL"), path=_pointer(path, "_schemaURL"))
    if "_deleted" in facet:
        if not isinstance(facet["_deleted"], bool):
            raise _validation_error(_pointer(path, "_deleted"), "value must be a boolean")
        if facet["_deleted"] is True:
            raise _validation_error(
                _pointer(path, "_deleted"),
                "supported facet deletion is not supported without temporal authority",
            )

    if key == "schema":
        if "fields" in facet:
            _validate_schema_fields(facet["fields"], path=_pointer(path, "fields"))
        return

    if key == "hierarchy":
        if "hierarchy" not in facet:
            raise _validation_error(_pointer(path, "hierarchy"), "missing required property")
        levels = _require_list(facet["hierarchy"], path=_pointer(path, "hierarchy"))
        for index, level in enumerate(levels):
            level_path = _pointer(path, "hierarchy", index)
            mapping = _require_mapping(level, path=level_path)
            _require_non_empty_str(mapping.get("type"), path=_pointer(level_path, "type"))
            _require_non_empty_str(mapping.get("name"), path=_pointer(level_path, "name"))
        return

    if key == "datasetType":
        _require_non_empty_str(
            facet.get("datasetType"),
            path=_pointer(path, "datasetType"),
        )
        if "subType" in facet and not isinstance(facet["subType"], str):
            raise _validation_error(
                _pointer(path, "subType"),
                "value must be a string",
            )
        return

    if key == "storage":
        _require_non_empty_str(
            facet.get("storageLayer"),
            path=_pointer(path, "storageLayer"),
        )
        if "fileFormat" in facet and not isinstance(facet["fileFormat"], str):
            raise _validation_error(
                _pointer(path, "fileFormat"),
                "value must be a string",
            )
        return

    if key == "ownership":
        if "owners" not in facet:
            raise _validation_error(_pointer(path, "owners"), "missing required property")
        owners = _require_list(facet["owners"], path=_pointer(path, "owners"))
        for index, owner in enumerate(owners):
            owner_path = _pointer(path, "owners", index)
            mapping = _require_mapping(owner, path=owner_path)
            _require_non_empty_str(mapping.get("name"), path=_pointer(owner_path, "name"))
            _require_non_empty_str(mapping.get("type"), path=_pointer(owner_path, "type"))
        return


def _validate_column_lineage_input_field(value: object, *, path: str) -> None:
    mapping = _require_mapping(value, path=path)
    _require_non_empty_str(mapping.get("namespace"), path=_pointer(path, "namespace"))
    _require_non_empty_str(mapping.get("name"), path=_pointer(path, "name"))
    _require_non_empty_str(mapping.get("field"), path=_pointer(path, "field"))
    if "transformations" not in mapping:
        return
    transformations = _require_list(
        mapping["transformations"], path=_pointer(path, "transformations")
    )
    for index, transformation in enumerate(transformations):
        t_path = _pointer(path, "transformations", index)
        t_mapping = _require_mapping(transformation, path=t_path)
        _require_non_empty_str(t_mapping.get("type"), path=_pointer(t_path, "type"))
        if "subtype" in t_mapping and not isinstance(t_mapping["subtype"], str):
            raise _validation_error(_pointer(t_path, "subtype"), "value must be a string")
        if "description" in t_mapping and not isinstance(t_mapping["description"], str):
            raise _validation_error(_pointer(t_path, "description"), "value must be a string")
        if "masking" in t_mapping and not isinstance(t_mapping["masking"], bool):
            raise _validation_error(_pointer(t_path, "masking"), "value must be a boolean")


def _validate_column_lineage_facet(facet: Mapping[str, Any], *, path: str) -> None:
    _require_uri_string(facet.get("_producer"), path=_pointer(path, "_producer"))
    schema_url = facet.get("_schemaURL")
    _require_uri_string(schema_url, path=_pointer(path, "_schemaURL"))
    if schema_url != _COLUMN_LINEAGE_SCHEMA_URL:
        raise _validation_error(
            _pointer(path, "_schemaURL"),
            "unsupported columnLineage facet schemaURL",
        )
    if "_deleted" in facet:
        if not isinstance(facet["_deleted"], bool):
            raise _validation_error(_pointer(path, "_deleted"), "value must be a boolean")
        if facet["_deleted"] is True:
            raise _validation_error(
                _pointer(path, "_deleted"),
                "supported facet deletion is not supported without temporal authority",
            )

    if "fields" not in facet:
        raise _validation_error(_pointer(path, "fields"), "missing required property")
    fields = _require_mapping(facet["fields"], path=_pointer(path, "fields"))
    for field_name, field_value in fields.items():
        field_path = _pointer(path, "fields", field_name)
        if not isinstance(field_name, str) or not field_name.strip():
            raise _validation_error(field_path, "value must be a non-empty string")
        field_mapping = _require_mapping(field_value, path=field_path)
        if "inputFields" not in field_mapping:
            raise _validation_error(
                _pointer(field_path, "inputFields"),
                "missing required property",
            )
        input_fields = _require_list(
            field_mapping["inputFields"], path=_pointer(field_path, "inputFields")
        )
        for index, input_field in enumerate(input_fields):
            _validate_column_lineage_input_field(
                input_field,
                path=_pointer(field_path, "inputFields", index),
            )
        if "transformationDescription" in field_mapping and not isinstance(
            field_mapping["transformationDescription"], str
        ):
            raise _validation_error(
                _pointer(field_path, "transformationDescription"),
                "value must be a string",
            )
        if "transformationType" in field_mapping and not isinstance(
            field_mapping["transformationType"], str
        ):
            raise _validation_error(
                _pointer(field_path, "transformationType"),
                "value must be a string",
            )

    if "dataset" in facet:
        dataset_items = _require_list(facet["dataset"], path=_pointer(path, "dataset"))
        for index, item in enumerate(dataset_items):
            _validate_column_lineage_input_field(item, path=_pointer(path, "dataset", index))


def _validate_dataset_facets(facets: object, *, path: str) -> None:
    mapping = _require_mapping(facets, path=path)
    for key, facet in mapping.items():
        facet_path = _pointer(path, key)
        facet_mapping = _require_mapping(facet, path=facet_path)
        if key == "columnLineage":
            _validate_column_lineage_facet(facet_mapping, path=facet_path)
        elif key in SUPPORTED_DATASET_FACETS:
            _validate_supported_facet(key, facet_mapping, path=facet_path)


def _validate_dataset(dataset: object, *, path: str) -> None:
    mapping = _require_mapping(dataset, path=path)
    _require_non_empty_str(mapping.get("namespace"), path=_pointer(path, "namespace"))
    _require_non_empty_str(mapping.get("name"), path=_pointer(path, "name"))
    if "facets" in mapping:
        _validate_dataset_facets(mapping["facets"], path=_pointer(path, "facets"))
    if "inputFacets" in mapping:
        _require_mapping(mapping["inputFacets"], path=_pointer(path, "inputFacets"))
    if "outputFacets" in mapping:
        _require_mapping(mapping["outputFacets"], path=_pointer(path, "outputFacets"))


def _validate_dataset_list(value: object, *, path: str) -> None:
    items = _require_list(value, path=path)
    for index, item in enumerate(items):
        _validate_dataset(item, path=_pointer(path, index))


def _validate_base_event(event: Mapping[str, Any], *, path: str) -> str:
    if "schemaURL" not in event:
        raise _validation_error(_pointer(path, "schemaURL"), "missing required property")
    schema_url = event["schemaURL"]
    if not isinstance(schema_url, str) or not schema_url.strip():
        raise _validation_error(
            _pointer(path, "schemaURL"),
            "value must be a non-empty string",
        )
    kind = _SUPPORTED_CORE_SCHEMA_URLS.get(schema_url)
    if kind is None:
        raise _unsupported_schema_error(_pointer(path, "schemaURL"))

    if "eventTime" not in event:
        raise _validation_error(_pointer(path, "eventTime"), "missing required property")
    _require_event_time(event["eventTime"], path=_pointer(path, "eventTime"))

    if "producer" not in event:
        raise _validation_error(_pointer(path, "producer"), "missing required property")
    _require_uri_string(event["producer"], path=_pointer(path, "producer"))
    return kind


def _validate_run_event(event: Mapping[str, Any], *, path: str) -> None:
    if "run" not in event:
        raise _validation_error(_pointer(path, "run"), "missing required property")
    run = _require_mapping(event["run"], path=_pointer(path, "run"))
    if "runId" not in run:
        raise _validation_error(_pointer(path, "run", "runId"), "missing required property")
    _require_uuid(run["runId"], path=_pointer(path, "run", "runId"))
    if "facets" in run:
        _require_mapping(run["facets"], path=_pointer(path, "run", "facets"))

    if "job" not in event:
        raise _validation_error(_pointer(path, "job"), "missing required property")
    _validate_job(event["job"], path=_pointer(path, "job"))

    if "eventType" in event:
        event_type = event["eventType"]
        if not isinstance(event_type, str) or event_type not in _EVENT_TYPES:
            raise _validation_error(
                _pointer(path, "eventType"),
                "value must be one of START, RUNNING, COMPLETE, ABORT, FAIL, OTHER",
            )

    if "inputs" in event:
        _validate_dataset_list(event["inputs"], path=_pointer(path, "inputs"))
    if "outputs" in event:
        _validate_dataset_list(event["outputs"], path=_pointer(path, "outputs"))


def _validate_job_event(event: Mapping[str, Any], *, path: str) -> None:
    if "run" in event:
        raise _validation_error(_pointer(path, "run"), "JobEvent must not include run")
    if "job" not in event:
        raise _validation_error(_pointer(path, "job"), "missing required property")
    _validate_job(event["job"], path=_pointer(path, "job"))
    if "inputs" in event:
        _validate_dataset_list(event["inputs"], path=_pointer(path, "inputs"))
    if "outputs" in event:
        _validate_dataset_list(event["outputs"], path=_pointer(path, "outputs"))


def _validate_dataset_event(event: Mapping[str, Any], *, path: str) -> None:
    if "run" in event:
        raise _validation_error(_pointer(path, "run"), "DatasetEvent must not include run")
    if "job" in event:
        raise _validation_error(_pointer(path, "job"), "DatasetEvent must not include job")
    if "dataset" not in event:
        raise _validation_error(_pointer(path, "dataset"), "missing required property")
    _validate_dataset(event["dataset"], path=_pointer(path, "dataset"))


def _validate_event(event: object, *, path: str) -> dict[str, Any]:
    mapping = _require_mapping(event, path=path)
    kind = _validate_base_event(mapping, path=path)
    if kind == _EVENT_KIND_RUN:
        _validate_run_event(mapping, path=path)
    elif kind == _EVENT_KIND_JOB:
        _validate_job_event(mapping, path=path)
    else:
        _validate_dataset_event(mapping, path=path)
    return mapping


def validate_openlineage_events(
    events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Validate OpenLineage events for this integration (core 2-0-2 subset).

    Returns a deep-copied validated tuple. Does not mutate the caller input.
    """
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes, bytearray)):
        raise _validation_error("", "events must be an array")

    copied_root = _copy_json_tree(list(events))
    if not isinstance(copied_root, list):
        raise _validation_error("", "events must be an array")

    validated: list[dict[str, Any]] = []
    for index, event in enumerate(copied_root):
        validated.append(_validate_event(event, path=_pointer("", index)))
    return tuple(validated)
