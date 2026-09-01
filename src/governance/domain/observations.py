"""Property observations captured at mapper time."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from governance.domain.graph import GovernanceGraph, GraphNodeIdentity, ProvenanceRecord
from governance.identity.canonicalize import canonical_json_bytes
from governance.identity.hashing import ContentIdentity, property_observation_set_identity
from governance.identity.json_values import (
    canonical_value_fingerprint,
    normalize_json_value,
)

PROPERTY_OBSERVATION_SET_SCHEMA = "governance-property-observations"
PROPERTY_OBSERVATION_SET_VERSION = "1"


@dataclass(frozen=True, slots=True)
class PropertyPath:
    """RFC6901 JSON Pointer path as immutable segments."""

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("PropertyPath requires at least one segment")
        for segment in self.segments:
            if not isinstance(segment, str):
                raise TypeError("PropertyPath segments must be strings")

    @classmethod
    def parse(cls, pointer: str) -> PropertyPath:
        if not isinstance(pointer, str):
            raise TypeError("PropertyPath.parse requires a string")
        if pointer == "":
            raise ValueError("root pointer is not a valid PropertyPath")
        if not pointer.startswith("/"):
            raise ValueError("JSON Pointer must start with '/'")
        raw_segments = pointer[1:].split("/")
        segments: list[str] = []
        for raw in raw_segments:
            decoded: list[str] = []
            index = 0
            while index < len(raw):
                char = raw[index]
                if char == "~":
                    if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                        raise ValueError("invalid JSON Pointer escape")
                    decoded.append("~" if raw[index + 1] == "0" else "/")
                    index += 2
                else:
                    decoded.append(char)
                    index += 1
            segments.append("".join(decoded))
        return cls(tuple(segments))

    def to_pointer(self) -> str:
        parts: list[str] = []
        for segment in self.segments:
            escaped = segment.replace("~", "~0").replace("/", "~1")
            parts.append(escaped)
        return "/" + "/".join(parts)


def _merge_provenance(records: Sequence[ProvenanceRecord]) -> tuple[ProvenanceRecord, ...]:
    unique: dict[ProvenanceRecord, None] = {}
    for record in records:
        unique[record] = None
    return tuple(sorted(unique.keys(), key=lambda record: record.canonical_sort_key()))


@dataclass(frozen=True, slots=True)
class PropertyObservation:
    """One semantic value for (object, path) with supporting provenance."""

    object_identity: GraphNodeIdentity
    property_path: PropertyPath
    value: Any
    provenance: tuple[ProvenanceRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.object_identity, GraphNodeIdentity):
            raise TypeError("object_identity must be GraphNodeIdentity")
        if not isinstance(self.property_path, PropertyPath):
            raise TypeError("property_path must be PropertyPath")
        object.__setattr__(self, "value", normalize_json_value(self.value))
        if not self.provenance:
            raise ValueError("PropertyObservation.provenance must be non-empty")
        for record in self.provenance:
            if not isinstance(record, ProvenanceRecord):
                raise TypeError("provenance entries must be ProvenanceRecord")
        object.__setattr__(self, "provenance", _merge_provenance(self.provenance))

    def value_fingerprint(self) -> bytes:
        return canonical_value_fingerprint(self.value)

    def grouping_key(self) -> tuple[bytes, str, bytes]:
        return (
            self.object_identity.canonical_bytes(),
            self.property_path.to_pointer(),
            self.value_fingerprint(),
        )

    def to_identity_entry(self) -> dict[str, Any]:
        return {
            "object": self.object_identity.to_dict(),
            "property": self.property_path.to_pointer(),
            "provenance": [record.to_dict() for record in self.provenance],
            "value": self.value,
        }

    def to_value_group_dict(self) -> dict[str, Any]:
        return {
            "provenance": [record.to_dict() for record in self.provenance],
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class PropertyObservationSet:
    """Normalized, deterministic set of property observations."""

    observations: tuple[PropertyObservation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observations",
            tuple(
                sorted(
                    self.observations,
                    key=lambda item: canonical_json_bytes(item.to_identity_entry()),
                )
            ),
        )

    @classmethod
    def from_observations(
        cls,
        observations: Iterable[PropertyObservation],
    ) -> PropertyObservationSet:
        groups: dict[tuple[bytes, str, bytes], list[PropertyObservation]] = {}
        for observation in observations:
            if not isinstance(observation, PropertyObservation):
                raise TypeError("observations must be PropertyObservation instances")
            groups.setdefault(observation.grouping_key(), []).append(observation)

        merged: list[PropertyObservation] = []
        for group in groups.values():
            base = group[0]
            provenance_records: list[ProvenanceRecord] = []
            for item in group:
                provenance_records.extend(item.provenance)
            merged.append(
                PropertyObservation(
                    object_identity=base.object_identity,
                    property_path=base.property_path,
                    value=base.value,
                    provenance=_merge_provenance(provenance_records),
                )
            )
        return cls(observations=tuple(merged))

    @staticmethod
    def merge(*sets: PropertyObservationSet) -> PropertyObservationSet:
        if not sets:
            return PropertyObservationSet()
        collected: list[PropertyObservation] = []
        for observation_set in sets:
            if not isinstance(observation_set, PropertyObservationSet):
                raise TypeError("merge arguments must be PropertyObservationSet")
            collected.extend(observation_set.observations)
        return PropertyObservationSet.from_observations(collected)

    def to_identity_dict(self) -> dict[str, Any]:
        return {
            "observation_schema": PROPERTY_OBSERVATION_SET_SCHEMA,
            "observation_version": PROPERTY_OBSERVATION_SET_VERSION,
            "observations": [item.to_identity_entry() for item in self.observations],
        }

    def content_identity(self) -> ContentIdentity:
        return property_observation_set_identity(self.to_identity_dict())


@dataclass
class PropertyObservationBuilder:
    """Accumulate mapper-time observations one provenance at a time."""

    _pending: list[tuple[GraphNodeIdentity, PropertyPath, Any, ProvenanceRecord]] = field(
        default_factory=list
    )

    def observe(
        self,
        object_identity: GraphNodeIdentity,
        property_path: PropertyPath,
        value: Any,
        provenance: ProvenanceRecord,
    ) -> None:
        if not isinstance(object_identity, GraphNodeIdentity):
            raise TypeError("object_identity must be GraphNodeIdentity")
        if not isinstance(property_path, PropertyPath):
            raise TypeError("property_path must be PropertyPath")
        if not isinstance(provenance, ProvenanceRecord):
            raise TypeError("provenance must be ProvenanceRecord")
        self._pending.append((object_identity, property_path, value, provenance))

    def build(self) -> PropertyObservationSet:
        observations = [
            PropertyObservation(
                object_identity=object_identity,
                property_path=property_path,
                value=value,
                provenance=(provenance,),
            )
            for object_identity, property_path, value, provenance in self._pending
        ]
        return PropertyObservationSet.from_observations(observations)


@dataclass(frozen=True, slots=True)
class GovernanceMappingResult:
    """In-memory carrier for graph + property observations (no own identity)."""

    graph: GovernanceGraph
    observations: PropertyObservationSet

    def __post_init__(self) -> None:
        if not isinstance(self.graph, GovernanceGraph):
            raise TypeError("graph must be GovernanceGraph")
        if not isinstance(self.observations, PropertyObservationSet):
            raise TypeError("observations must be PropertyObservationSet")
