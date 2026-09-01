"""Property path and value compatibility for comparison object kinds."""

from __future__ import annotations

import math
from typing import Any

from governance.comparison.projection import ComparisonObjectIdentity, GovernedObjectKind
from governance.domain.observations import PropertyPath
from governance.identity.json_values import validate_json_value

_FIXED_PATHS: dict[GovernedObjectKind, frozenset[str]] = {
    "data_source": frozenset({"/name", "/system_type", "/description", "/ownership"}),
    "database": frozenset({"/name", "/description", "/ownership"}),
    "schema": frozenset({"/name", "/description", "/ownership"}),
    "table": frozenset({"/name", "/description", "/ownership"}),
    "column": frozenset({"/name", "/data_type", "/ordinal_position", "/nullable", "/description"}),
    "primary_key": frozenset({"/name", "/column_ids"}),
    "foreign_key": frozenset(
        {"/name", "/column_ids", "/referenced_table_id", "/referenced_column_ids"}
    ),
    "relationship": frozenset({"/name", "/to_table_id", "/foreign_key_id", "/description"}),
}

_TECHNICAL_ATTRIBUTES_KINDS: frozenset[GovernedObjectKind] = frozenset(
    {"data_source", "table", "column"}
)

_NAME_ALLOWED_KINDS: frozenset[GovernedObjectKind] = frozenset({"data_source", "database"})


def property_path_compatible_with_kind(kind: GovernedObjectKind, pointer: str) -> bool:
    """Return whether ``pointer`` is a valid projection property shape for ``kind``."""
    if pointer.startswith("/technical_attributes/"):
        if kind not in _TECHNICAL_ATTRIBUTES_KINDS:
            return False
        try:
            parsed = PropertyPath.parse(pointer)
        except (TypeError, ValueError):
            return False
        return (
            len(parsed.segments) == 2
            and parsed.segments[0] == "technical_attributes"
            and isinstance(parsed.segments[1], str)
        )
    return pointer in _FIXED_PATHS[kind]


def comparison_change_property_compatible_with_kind(kind: GovernedObjectKind, pointer: str) -> bool:
    """Return whether ``pointer`` may appear in a successful comparison artifact."""
    if kind == "data_source" and pointer == "/system_type":
        return False
    if pointer == "/name" and kind not in _NAME_ALLOWED_KINDS:
        return False
    return property_path_compatible_with_kind(kind, pointer)


def _is_technical_attribute_pointer(pointer: str) -> bool:
    return pointer.startswith("/technical_attributes/")


def _non_whitespace_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _parse_identity_dict(raw: Any) -> ComparisonObjectIdentity | None:
    if not isinstance(raw, dict) or set(raw) != {"kind", "path"}:
        return None
    if not isinstance(raw["path"], list):
        return None
    try:
        return ComparisonObjectIdentity(kind=raw["kind"], path=tuple(raw["path"]))
    except (KeyError, TypeError, ValueError):
        return None


def _valid_strict_json_value(value: Any) -> bool:
    try:
        validate_json_value(value)
    except (TypeError, ValueError):
        return False
    return True


def _valid_ownership_value(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {"owner_name", "owner_type"}:
        return False
    return _non_whitespace_string(value["owner_name"]) and _non_whitespace_string(
        value["owner_type"]
    )


def _valid_column_identity_list(
    value: Any,
    *,
    require_non_empty: bool,
    required_table_prefix: tuple[str, str] | None = None,
    require_same_table_prefix: bool = False,
) -> bool:
    if not isinstance(value, list):
        return False
    if require_non_empty and len(value) == 0:
        return False
    shared_prefix: tuple[str, str] | None = None
    for item in value:
        identity = _parse_identity_dict(item)
        if identity is None or identity.kind != "column":
            return False
        if required_table_prefix is not None and identity.path[:2] != required_table_prefix:
            return False
        if require_same_table_prefix:
            prefix = identity.path[:2]
            if shared_prefix is None:
                shared_prefix = prefix
            elif prefix != shared_prefix:
                return False
    return True


def _valid_table_identity(value: Any) -> bool:
    identity = _parse_identity_dict(value)
    return identity is not None and identity.kind == "table"


def _valid_foreign_key_identity(
    value: Any,
    *,
    relationship_table_prefix: tuple[str, str] | None = None,
) -> bool:
    if value is None:
        return True
    identity = _parse_identity_dict(value)
    if identity is None or identity.kind != "foreign_key":
        return False
    return relationship_table_prefix is None or identity.path[:2] == relationship_table_prefix


def _valid_fixed_value(pointer: str, value: Any, identity: ComparisonObjectIdentity) -> bool:
    if pointer == "/name":
        return _non_whitespace_string(value)
    if pointer == "/description":
        return _valid_strict_json_value(value)
    if pointer == "/ownership":
        return _valid_ownership_value(value)
    if pointer == "/data_type":
        return isinstance(value, str) and value.strip() != ""
    if pointer == "/ordinal_position":
        return type(value) is int and value >= 1
    if pointer == "/nullable":
        return type(value) is bool
    if pointer == "/column_ids":
        return _valid_column_identity_list(
            value,
            require_non_empty=True,
            required_table_prefix=identity.path[:2],
        )
    if pointer == "/referenced_table_id":
        return _valid_table_identity(value)
    if pointer == "/referenced_column_ids":
        return _valid_column_identity_list(
            value,
            require_non_empty=True,
            require_same_table_prefix=True,
        )
    if pointer == "/to_table_id":
        return _valid_table_identity(value)
    if pointer == "/foreign_key_id":
        return _valid_foreign_key_identity(
            value,
            relationship_table_prefix=identity.path[:2],
        )
    return False


def comparable_change_value_compatible(
    identity: ComparisonObjectIdentity,
    pointer: str,
    side: dict[str, Any],
) -> bool:
    """Return whether a comparison property side matches producer output semantics."""
    if not isinstance(side, dict):
        return False
    has_value = side.get("has_value")
    if has_value is True:
        if "value" not in side or len(side) != 2:
            return False
        value = side["value"]
        if isinstance(value, float) and not math.isfinite(value):
            return False
    elif has_value is False:
        if len(side) != 1 or "value" in side:
            return False
        return _is_technical_attribute_pointer(pointer)
    else:
        return False

    if _is_technical_attribute_pointer(pointer):
        return _valid_strict_json_value(value)

    if not comparison_change_property_compatible_with_kind(identity.kind, pointer):
        return False
    return _valid_fixed_value(pointer, value, identity)


def foreign_key_reference_sides_coherent(
    property_changes: list[dict[str, Any]],
) -> bool:
    """Validate referenced_table_id vs referenced_column_ids prefix coherence per side."""
    table_change = next(
        (item for item in property_changes if item.get("property") == "/referenced_table_id"),
        None,
    )
    column_change = next(
        (item for item in property_changes if item.get("property") == "/referenced_column_ids"),
        None,
    )
    if table_change is None or column_change is None:
        return True

    for side_name in ("baseline", "candidate"):
        table_side = table_change.get(side_name)
        column_side = column_change.get(side_name)
        if not isinstance(table_side, dict) or not isinstance(column_side, dict):
            return False
        if not table_side.get("has_value") or not column_side.get("has_value"):
            continue
        table_identity = _parse_identity_dict(table_side["value"])
        if table_identity is None:
            return False
        table_prefix = table_identity.path
        column_values = column_side.get("value")
        if not isinstance(column_values, list):
            return False
        for item in column_values:
            column_identity = _parse_identity_dict(item)
            if column_identity is None or column_identity.path[:2] != table_prefix:
                return False
    return True
