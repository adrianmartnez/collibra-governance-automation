"""Reconciliation target map and typed value conversion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from governance.domain.graph import (
    NODE_KIND_COLUMN,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    GraphNodeIdentity,
)
from governance.domain.observations import PropertyPath
from governance.identity.json_values import canonical_value_fingerprint, normalize_json_value

PATH_NAME = PropertyPath(("name",))
PATH_DESCRIPTION = PropertyPath(("description",))
PATH_DATA_TYPE = PropertyPath(("attributes", "data_type"))
PATH_OWNERSHIP = PropertyPath(("attributes", "ownership"))

TargetField = Literal["display_name", "description", "data_type", "owner"]


@dataclass(frozen=True, slots=True)
class RepresentableTargetValue:
    """Typed Collibra target value after conversion."""

    field: TargetField
    has_value: bool
    value: str | None = None


def is_ownership_applicable(kind: str) -> bool:
    return kind in {NODE_KIND_DATA_SOURCE, NODE_KIND_DATASET, NODE_KIND_TABLE}


def is_data_type_applicable(kind: str) -> bool:
    return kind == NODE_KIND_COLUMN


def mapped_property_paths_for_kind(kind: str) -> tuple[PropertyPath, ...]:
    paths: list[PropertyPath] = [PATH_NAME, PATH_DESCRIPTION]
    if is_data_type_applicable(kind):
        paths.append(PATH_DATA_TYPE)
    if is_ownership_applicable(kind):
        paths.append(PATH_OWNERSHIP)
    return tuple(paths)


def target_field_for_path(path: PropertyPath) -> TargetField | None:
    pointer = path.to_pointer()
    if pointer == "/name":
        return "display_name"
    if pointer == "/description":
        return "description"
    if pointer == "/attributes/data_type":
        return "data_type"
    if pointer == "/attributes/ownership":
        return "owner"
    return None


def path_applicable_to_identity(path: PropertyPath, identity: GraphNodeIdentity) -> bool:
    field = target_field_for_path(path)
    if field is None:
        return False
    if field == "data_type":
        return is_data_type_applicable(identity.kind)
    if field == "owner":
        return is_ownership_applicable(identity.kind)
    return True


def convert_effective_value(
    *,
    path: PropertyPath,
    identity: GraphNodeIdentity,
    value: Any,
    has_effective_value: bool,
) -> RepresentableTargetValue | None:
    """Return representable target or ``None`` if unsupported/unapplicable.

    ``None`` return means ``unsupported_effective_value`` (or not mapped).
    """
    if not path_applicable_to_identity(path, identity):
        return None
    field = target_field_for_path(path)
    if field is None:
        return None
    if not has_effective_value:
        # Should not be called without an effective value; treat as unsupported.
        return None

    normalized = normalize_json_value(value)

    if field == "display_name":
        if normalized is None:
            return RepresentableTargetValue(field=field, has_value=False, value=None)
        if isinstance(normalized, str) and normalized.strip():
            return RepresentableTargetValue(field=field, has_value=True, value=normalized)
        return None

    if field == "description":
        if normalized is None:
            return RepresentableTargetValue(field=field, has_value=False, value=None)
        if isinstance(normalized, str):
            return RepresentableTargetValue(field=field, has_value=True, value=normalized)
        return None

    if field == "data_type":
        if normalized is None:
            return RepresentableTargetValue(field=field, has_value=False, value=None)
        if isinstance(normalized, str) and normalized.strip():
            return RepresentableTargetValue(field=field, has_value=True, value=normalized)
        return None

    if field == "owner":
        if normalized is None:
            return RepresentableTargetValue(field=field, has_value=False, value=None)
        if not isinstance(normalized, list) or len(normalized) != 1:
            return None
        owner = normalized[0]
        if not isinstance(owner, dict):
            return None
        name = owner.get("name")
        owner_type = owner.get("type")
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(owner_type, str):
            return None
        # Require name+type keys present; extra keys allowed if domain emitted them,
        # but reject if name/type missing. Plan: keys include name and type.
        if "name" not in owner or "type" not in owner:
            return None
        return RepresentableTargetValue(field=field, has_value=True, value=name)

    return None


def values_equal(left: Any, right: Any) -> bool:
    return canonical_value_fingerprint(left) == canonical_value_fingerprint(right)
