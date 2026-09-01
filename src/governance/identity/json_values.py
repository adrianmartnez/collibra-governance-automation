"""Typed JSON canonicalize shared by graph attributes and property observations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from governance.identity.canonicalize import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class CanonicalObject:
    """Tagged immutable JSON object (distinct from array)."""

    items: tuple[tuple[str, Any], ...]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CanonicalObject):
            return NotImplemented
        # Use JSON bytes so bool True and int 1 remain distinct (bool subclasses int).
        return canonical_value_fingerprint(self) == canonical_value_fingerprint(other)

    def __hash__(self) -> int:
        return hash(canonical_value_fingerprint(self))


@dataclass(frozen=True, slots=True)
class CanonicalArray:
    """Tagged immutable JSON array (distinct from object; order material)."""

    items: tuple[Any, ...]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CanonicalArray):
            return NotImplemented
        return canonical_value_fingerprint(self) == canonical_value_fingerprint(other)

    def __hash__(self) -> int:
        return hash(canonical_value_fingerprint(self))


def canonicalize_json_value(value: object) -> Any:
    """Validate and canonicalize a JSON value into tagged/plain forms.

    Accepts: None, bool, int, finite float, str, Mapping (str keys),
    Sequence except str/bytes/bytearray.
    Rejects: NaN/±Inf, non-string keys, set/frozenset, bytes/bytearray, unsupported.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("float attributes must be finite (reject NaN/±Infinity)")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        items: list[tuple[str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            items.append((key, canonicalize_json_value(item)))
        items.sort(key=lambda pair: pair[0])
        return CanonicalObject(tuple(items))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return CanonicalArray(tuple(canonicalize_json_value(item) for item in value))
    raise TypeError(f"unsupported JSON attribute type: {type(value).__name__}")


def canonicalize_attributes_object(attributes: object | None) -> CanonicalObject:
    if attributes is None:
        return CanonicalObject(())
    if isinstance(attributes, CanonicalObject):
        return attributes
    if not isinstance(attributes, Mapping):
        raise TypeError("attributes root must be a JSON object (mapping)")
    canonical = canonicalize_json_value(attributes)
    if not isinstance(canonical, CanonicalObject):
        raise TypeError("attributes root must be a JSON object (mapping)")
    return canonical


def canonical_json_to_plain(value: Any) -> Any:
    if isinstance(value, CanonicalObject):
        return {key: canonical_json_to_plain(item) for key, item in value.items}
    if isinstance(value, CanonicalArray):
        return [canonical_json_to_plain(item) for item in value.items]
    return value


def normalize_json_value(value: object) -> Any:
    """Validate ``value`` and return plain JSON-compatible Python objects."""
    return canonical_json_to_plain(canonicalize_json_value(value))


def canonical_value_fingerprint(value: Any) -> bytes:
    """Deterministic fingerprint for tagged or plain JSON values."""
    return canonical_json_bytes(canonical_json_to_plain(value))
