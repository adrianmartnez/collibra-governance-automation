"""Validate the consumed dbt Manifest v12 subset (no full schema vendoring).

Supported input contract: dbt Manifest schema v12 subset only.
Schema URI: https://schemas.getdbt.com/dbt/manifest/v12.json
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from governance.integrations.dbt.errors import (
    CODE_UNSUPPORTED_MANIFEST_VERSION,
    CODE_VALIDATION,
    DbtDiagnostic,
    DbtUnsupportedManifestVersionError,
    DbtValidationError,
)

SUPPORTED_MANIFEST_SCHEMA_URI = "https://schemas.getdbt.com/dbt/manifest/v12.json"
_MAX_JSON_TREE_DEPTH = 64

_RESOURCE_TYPE_MODEL = "model"
_RESOURCE_TYPE_SOURCE = "source"


def _validation_error(path: str, message: str) -> DbtValidationError:
    return DbtValidationError(
        [
            DbtDiagnostic(
                code=CODE_VALIDATION,
                path=path,
                message=message,
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


def _optional_tags(value: object, *, path: str) -> None:
    tags = _require_list(value, path=path)
    for index, tag in enumerate(tags):
        if not isinstance(tag, str):
            raise _validation_error(_pointer(path, index), "value must be a string")


def _optional_meta(value: object, *, path: str) -> None:
    _require_mapping(value, path=path)


def _sorted_mapping_items(mapping: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Deterministic fail-fast iteration over mapping keys."""
    return [(key, mapping[key]) for key in sorted(mapping.keys())]


def _validate_columns(columns: object, *, path: str) -> None:
    mapping = _require_mapping(columns, path=path)
    for key, column in _sorted_mapping_items(mapping):
        if not isinstance(key, str) or not key:
            raise _validation_error(path, "column mapping keys must be non-empty strings")
        col_path = _pointer(path, key)
        col = _require_mapping(column, path=col_path)
        name = _require_non_empty_str(col.get("name"), path=_pointer(col_path, "name"))
        if key != name:
            raise _validation_error(
                col_path,
                "column mapping key must equal ColumnInfo.name",
            )
        if (
            "description" in col
            and col["description"] is not None
            and not isinstance(col["description"], str)
        ):
            raise _validation_error(
                _pointer(col_path, "description"),
                "value must be a string or null",
            )
        if (
            "data_type" in col
            and col["data_type"] is not None
            and not isinstance(col["data_type"], str)
        ):
            raise _validation_error(
                _pointer(col_path, "data_type"),
                "value must be a string or null",
            )
        if "quote" in col and col["quote"] is not None and not isinstance(col["quote"], bool):
            raise _validation_error(
                _pointer(col_path, "quote"),
                "value must be a boolean or null",
            )
        if "tags" in col:
            _optional_tags(col["tags"], path=_pointer(col_path, "tags"))
        if "meta" in col:
            _optional_meta(col["meta"], path=_pointer(col_path, "meta"))
        if "constraints" in col:
            constraints = _require_list(col["constraints"], path=_pointer(col_path, "constraints"))
            for index, item in enumerate(constraints):
                _require_mapping(item, path=_pointer(col_path, "constraints", index))


def _validate_database(value: object, *, path: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise _validation_error(path, "value must be a string or null")
    # Empty string is allowed structurally; mapping treats it as absent.


def _validate_fqn(value: object, *, path: str) -> None:
    fqn = _require_list(value, path=path)
    if not fqn:
        raise _validation_error(path, "value must be a non-empty array")
    for index, part in enumerate(fqn):
        if not isinstance(part, str) or not part:
            raise _validation_error(
                _pointer(path, index),
                "value must be a non-empty string",
            )


def _validate_model(node: Mapping[str, Any], *, path: str) -> None:
    _require_non_empty_str(node.get("name"), path=_pointer(path, "name"))
    _require_non_empty_str(node.get("package_name"), path=_pointer(path, "package_name"))
    _require_non_empty_str(node.get("schema"), path=_pointer(path, "schema"))
    _require_non_empty_str(node.get("alias"), path=_pointer(path, "alias"))
    _validate_database(node.get("database"), path=_pointer(path, "database"))
    _validate_fqn(node.get("fqn"), path=_pointer(path, "fqn"))
    config = _require_mapping(node.get("config"), path=_pointer(path, "config"))
    if "materialized" in config and not isinstance(config["materialized"], str):
        raise _validation_error(
            _pointer(path, "config", "materialized"),
            "value must be a string",
        )
    if (
        "description" in node
        and node["description"] is not None
        and not isinstance(node["description"], str)
    ):
        raise _validation_error(
            _pointer(path, "description"),
            "value must be a string or null",
        )
    if "columns" in node:
        _validate_columns(node["columns"], path=_pointer(path, "columns"))
    if "tags" in node:
        _optional_tags(node["tags"], path=_pointer(path, "tags"))
    if "meta" in node:
        _optional_meta(node["meta"], path=_pointer(path, "meta"))


def _validate_source(source: Mapping[str, Any], *, path: str) -> None:
    _require_non_empty_str(source.get("name"), path=_pointer(path, "name"))
    _require_non_empty_str(source.get("package_name"), path=_pointer(path, "package_name"))
    _require_non_empty_str(source.get("schema"), path=_pointer(path, "schema"))
    _require_non_empty_str(source.get("identifier"), path=_pointer(path, "identifier"))
    _require_non_empty_str(source.get("source_name"), path=_pointer(path, "source_name"))
    _validate_database(source.get("database"), path=_pointer(path, "database"))
    if "fqn" in source:
        _validate_fqn(source["fqn"], path=_pointer(path, "fqn"))
    if (
        "description" in source
        and source["description"] is not None
        and not isinstance(source["description"], str)
    ):
        raise _validation_error(
            _pointer(path, "description"),
            "value must be a string or null",
        )
    if (
        "loader" in source
        and source["loader"] is not None
        and not isinstance(source["loader"], str)
    ):
        raise _validation_error(
            _pointer(path, "loader"),
            "value must be a string or null",
        )
    if "columns" in source:
        _validate_columns(source["columns"], path=_pointer(path, "columns"))
    if "tags" in source:
        _optional_tags(source["tags"], path=_pointer(path, "tags"))
    if "meta" in source:
        _optional_meta(source["meta"], path=_pointer(path, "meta"))


def _validate_resource_entry(
    key: str,
    resource: object,
    *,
    collection_path: str,
    expected_type: str | None,
) -> None:
    if not isinstance(key, str) or not key:
        raise _validation_error(collection_path, "resource mapping keys must be non-empty strings")
    path = _pointer(collection_path, key)
    node = _require_mapping(resource, path=path)
    unique_id = _require_non_empty_str(node.get("unique_id"), path=_pointer(path, "unique_id"))
    if key != unique_id:
        raise _validation_error(
            path,
            "dictionary key must equal resource unique_id",
        )
    resource_type = _require_non_empty_str(
        node.get("resource_type"),
        path=_pointer(path, "resource_type"),
    )
    if expected_type is not None and resource_type != expected_type:
        raise _validation_error(
            _pointer(path, "resource_type"),
            f"value must be {expected_type!r}",
        )
    if resource_type == _RESOURCE_TYPE_MODEL:
        _validate_model(node, path=path)
    elif resource_type == _RESOURCE_TYPE_SOURCE:
        _validate_source(node, path=path)
    # Unsupported node resource types are ignored by design.


def _validate_parent_map(parent_map: Mapping[str, Any], *, path: str) -> None:
    for key, parents in _sorted_mapping_items(parent_map):
        if not isinstance(key, str) or not key:
            raise _validation_error(path, "parent_map keys must be non-empty strings")
        key_path = _pointer(path, key)
        parent_list = _require_list(parents, path=key_path)
        for index, ref in enumerate(parent_list):
            if not isinstance(ref, str) or not ref:
                raise _validation_error(
                    _pointer(key_path, index),
                    "value must be a non-empty string",
                )


def _validate_disabled(disabled: Mapping[str, Any], *, path: str) -> None:
    for key, entries in _sorted_mapping_items(disabled):
        if not isinstance(key, str) or not key:
            raise _validation_error(path, "disabled keys must be non-empty strings")
        _require_list(entries, path=_pointer(path, key))


def _gate_manifest_version(document: Mapping[str, Any]) -> None:
    if "metadata" not in document:
        raise _validation_error("/metadata", "missing required property")
    metadata = _require_mapping(document["metadata"], path="/metadata")
    if "dbt_schema_version" not in metadata:
        raise _validation_error("/metadata/dbt_schema_version", "missing required property")
    schema_version = metadata["dbt_schema_version"]
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise _validation_error(
            "/metadata/dbt_schema_version",
            "value must be a non-empty string",
        )
    if schema_version != SUPPORTED_MANIFEST_SCHEMA_URI:
        raise DbtUnsupportedManifestVersionError(
            [
                DbtDiagnostic(
                    code=CODE_UNSUPPORTED_MANIFEST_VERSION,
                    path="/metadata/dbt_schema_version",
                    message="unsupported dbt Manifest schema version",
                )
            ]
        )
    if "dbt_version" not in metadata:
        raise _validation_error("/metadata/dbt_version", "missing required property")
    _require_non_empty_str(metadata["dbt_version"], path="/metadata/dbt_version")


def validate_dbt_manifest(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a dbt Manifest for this integration (Manifest v12 subset only).

    Returns a deep-copied validated mapping. Does not mutate the caller input.
    """
    copied = _copy_json_tree(document)
    if not isinstance(copied, dict):
        raise _validation_error("", "dbt Manifest root must be a mapping")

    _gate_manifest_version(copied)

    for key in ("nodes", "sources", "parent_map", "disabled"):
        if key not in copied:
            raise _validation_error(f"/{key}", "missing required property")

    nodes = _require_mapping(copied["nodes"], path="/nodes")
    sources = _require_mapping(copied["sources"], path="/sources")
    parent_map = _require_mapping(copied["parent_map"], path="/parent_map")
    disabled = _require_mapping(copied["disabled"], path="/disabled")

    for key, resource in _sorted_mapping_items(nodes):
        _validate_resource_entry(key, resource, collection_path="/nodes", expected_type=None)

    for key, resource in _sorted_mapping_items(sources):
        _validate_resource_entry(
            key,
            resource,
            collection_path="/sources",
            expected_type=_RESOURCE_TYPE_SOURCE,
        )

    _validate_parent_map(parent_map, path="/parent_map")
    _validate_disabled(disabled, path="/disabled")
    return copied
