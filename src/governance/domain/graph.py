"""Vendor-neutral governance graph and provenance model.

Internal domain foundation for contracts, lineage, and later impact analysis.
Independent of provider parsers and operational sync flows.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from governance.identity.canonicalize import canonical_json_bytes

NODE_KIND_DATA_SOURCE = "data_source"
NODE_KIND_DATASET = "dataset"
NODE_KIND_TABLE = "table"
NODE_KIND_COLUMN = "column"
NODE_KIND_CONTRACT = "contract"
NODE_KIND_TRANSFORMATION = "transformation"

EDGE_KIND_CONTAINS = "contains"
EDGE_KIND_DEPENDS_ON = "depends_on"
EDGE_KIND_GOVERNS = "governs"

_OBSERVATION_MODES = frozenset({"declared", "observed", "derived"})


def _require_non_empty_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


@dataclass(frozen=True, slots=True)
class _CanonicalObject:
    """Tagged immutable JSON object (distinct from array)."""

    items: tuple[tuple[str, Any], ...]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _CanonicalObject):
            return NotImplemented
        # Use JSON bytes so bool True and int 1 remain distinct (bool subclasses int).
        return _canonical_fingerprint(self) == _canonical_fingerprint(other)

    def __hash__(self) -> int:
        return hash(_canonical_fingerprint(self))


@dataclass(frozen=True, slots=True)
class _CanonicalArray:
    """Tagged immutable JSON array (distinct from object; order material)."""

    items: tuple[Any, ...]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _CanonicalArray):
            return NotImplemented
        return _canonical_fingerprint(self) == _canonical_fingerprint(other)

    def __hash__(self) -> int:
        return hash(_canonical_fingerprint(self))


def _canonical_fingerprint(value: Any) -> bytes:
    return canonical_json_bytes(_canonical_json_to_plain(value))


def _canonicalize_json_value(value: object) -> Any:
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
            items.append((key, _canonicalize_json_value(item)))
        items.sort(key=lambda pair: pair[0])
        return _CanonicalObject(tuple(items))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _CanonicalArray(tuple(_canonicalize_json_value(item) for item in value))
    raise TypeError(f"unsupported JSON attribute type: {type(value).__name__}")


def _canonicalize_attributes_object(attributes: object | None) -> _CanonicalObject:
    if attributes is None:
        return _CanonicalObject(())
    if isinstance(attributes, _CanonicalObject):
        return attributes
    if not isinstance(attributes, Mapping):
        raise TypeError("attributes root must be a JSON object (mapping)")
    canonical = _canonicalize_json_value(attributes)
    if not isinstance(canonical, _CanonicalObject):
        raise TypeError("attributes root must be a JSON object (mapping)")
    return canonical


def _canonical_json_to_plain(value: Any) -> Any:
    if isinstance(value, _CanonicalObject):
        return {key: _canonical_json_to_plain(item) for key, item in value.items}
    if isinstance(value, _CanonicalArray):
        return [_canonical_json_to_plain(item) for item in value.items]
    return value


def _merge_provenance(records: Sequence[ProvenanceRecord]) -> tuple[ProvenanceRecord, ...]:
    unique: dict[ProvenanceRecord, None] = {}
    for record in records:
        unique[record] = None
    return tuple(sorted(unique.keys(), key=lambda record: record.canonical_sort_key()))


@dataclass(frozen=True, slots=True)
class GraphNodeIdentity:
    """Stable vendor-neutral node identity.

    ``namespace`` is the logical identity scope of the governance graph. It is
    not a provider type (``postgresql`` / ``dbt`` / ``odcs`` / ``openlineage``);
    observation source belongs on :class:`ProvenanceRecord`.
    """

    namespace: str
    kind: str
    logical_id: str
    parent: GraphNodeIdentity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "namespace", _require_non_empty_str(self.namespace, "namespace"))
        object.__setattr__(self, "kind", _require_non_empty_str(self.kind, "kind"))
        object.__setattr__(
            self, "logical_id", _require_non_empty_str(self.logical_id, "logical_id")
        )
        if self.parent is not None and not isinstance(self.parent, GraphNodeIdentity):
            raise TypeError("parent must be GraphNodeIdentity or None")
        if self.kind == NODE_KIND_COLUMN and self.parent is None:
            raise ValueError("column identity requires parent")

    def to_canonical_list(self) -> list[Any]:
        return [
            self.namespace,
            self.kind,
            self.logical_id,
            None if self.parent is None else self.parent.to_canonical_list(),
        ]

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_list())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "logical_id": self.logical_id,
            "namespace": self.namespace,
            "parent": None if self.parent is None else self.parent.to_dict(),
        }

    def ancestors(self) -> tuple[GraphNodeIdentity, ...]:
        chain: list[GraphNodeIdentity] = []
        current = self.parent
        seen: set[GraphNodeIdentity] = set()
        while current is not None:
            if current in seen:
                break
            seen.add(current)
            chain.append(current)
            current = current.parent
        return tuple(chain)


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    """Explicit provenance for declared/observed/derived metadata."""

    provider_type: str
    source_ref: str
    source_version: str | None = None
    observation_mode: str = "observed"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_type",
            _require_non_empty_str(self.provider_type, "provider_type"),
        )
        object.__setattr__(
            self, "source_ref", _require_non_empty_str(self.source_ref, "source_ref")
        )
        if self.source_version is not None:
            if not isinstance(self.source_version, str) or not self.source_version.strip():
                raise ValueError("source_version must be None or a non-empty string")
            object.__setattr__(self, "source_version", self.source_version.strip())
        mode = _require_non_empty_str(self.observation_mode, "observation_mode")
        if mode not in _OBSERVATION_MODES:
            raise ValueError("observation_mode must be one of: declared, observed, derived")
        object.__setattr__(self, "observation_mode", mode)

    def to_canonical_list(self) -> list[Any]:
        return [
            self.provider_type,
            self.source_ref,
            self.source_version,
            self.observation_mode,
        ]

    def canonical_sort_key(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_list())

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_mode": self.observation_mode,
            "provider_type": self.provider_type,
            "source_ref": self.source_ref,
            "source_version": self.source_version,
        }


@dataclass(frozen=True, slots=True)
class GraphNode:
    """Immutable governance graph node with canonical attributes."""

    identity: GraphNodeIdentity
    name: str
    description: str | None = None
    attributes: Any = None
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, GraphNodeIdentity):
            raise TypeError("identity must be GraphNodeIdentity")
        object.__setattr__(self, "name", _require_non_empty_str(self.name, "name"))
        if self.description is not None and not isinstance(self.description, str):
            raise TypeError("description must be str or None")
        object.__setattr__(self, "attributes", _canonicalize_attributes_object(self.attributes))
        provenance = (
            tuple(self.provenance) if not isinstance(self.provenance, tuple) else self.provenance
        )
        for record in provenance:
            if not isinstance(record, ProvenanceRecord):
                raise TypeError("provenance entries must be ProvenanceRecord")
        object.__setattr__(self, "provenance", _merge_provenance(provenance))

    @property
    def attributes_canonical(self) -> _CanonicalObject:
        assert isinstance(self.attributes, _CanonicalObject)
        return self.attributes

    def material_payload(self) -> tuple[str, str | None, _CanonicalObject]:
        return (self.name, self.description, self.attributes_canonical)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attributes": _canonical_json_to_plain(self.attributes_canonical),
            "description": self.description,
            "identity": self.identity.to_dict(),
            "name": self.name,
            "provenance": [record.to_dict() for record in self.provenance],
        }


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """Immutable directed edge with stable logical identity."""

    source: GraphNodeIdentity
    target: GraphNodeIdentity
    kind: str
    attributes: Any = None
    provenance: tuple[ProvenanceRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source, GraphNodeIdentity):
            raise TypeError("source must be GraphNodeIdentity")
        if not isinstance(self.target, GraphNodeIdentity):
            raise TypeError("target must be GraphNodeIdentity")
        object.__setattr__(self, "kind", _require_non_empty_str(self.kind, "kind"))
        object.__setattr__(self, "attributes", _canonicalize_attributes_object(self.attributes))
        provenance = (
            tuple(self.provenance) if not isinstance(self.provenance, tuple) else self.provenance
        )
        for record in provenance:
            if not isinstance(record, ProvenanceRecord):
                raise TypeError("provenance entries must be ProvenanceRecord")
        object.__setattr__(self, "provenance", _merge_provenance(provenance))

    @property
    def attributes_canonical(self) -> _CanonicalObject:
        assert isinstance(self.attributes, _CanonicalObject)
        return self.attributes

    def logical_identity(self) -> tuple[GraphNodeIdentity, str, GraphNodeIdentity]:
        return (self.source, self.kind, self.target)

    def logical_sort_key(self) -> bytes:
        return canonical_json_bytes(
            [
                self.source.to_canonical_list(),
                self.kind,
                self.target.to_canonical_list(),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "attributes": _canonical_json_to_plain(self.attributes_canonical),
            "kind": self.kind,
            "provenance": [record.to_dict() for record in self.provenance],
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
        }


def _node_sort_key(node: GraphNode) -> bytes:
    return node.identity.canonical_bytes()


def _normalize_governance_graph(
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
) -> tuple[tuple[GraphNode, ...], tuple[GraphEdge, ...]]:
    """Validate, merge, and deterministically order graph parts."""
    node_groups: dict[GraphNodeIdentity, list[GraphNode]] = {}
    for node in nodes:
        if not isinstance(node, GraphNode):
            raise TypeError("nodes must be GraphNode instances")
        node_groups.setdefault(node.identity, []).append(node)

    merged_nodes: list[GraphNode] = []
    for identity, group in node_groups.items():
        base = group[0]
        base_payload = base.material_payload()
        provenance_records: list[ProvenanceRecord] = list(base.provenance)
        for other in group[1:]:
            if other.material_payload() != base_payload:
                raise ValueError(
                    f"conflicting GraphNode payload for identity {identity.to_canonical_list()!r}"
                )
            provenance_records.extend(other.provenance)
        merged_nodes.append(
            GraphNode(
                identity=identity,
                name=base.name,
                description=base.description,
                attributes=_canonical_json_to_plain(base.attributes_canonical),
                provenance=_merge_provenance(provenance_records),
            )
        )

    index = {node.identity: node for node in merged_nodes}
    for node in merged_nodes:
        for ancestor in node.identity.ancestors():
            if ancestor not in index:
                raise ValueError(
                    f"parent identity not present in graph: {ancestor.to_canonical_list()!r}"
                )

    edge_groups: dict[tuple[GraphNodeIdentity, str, GraphNodeIdentity], list[GraphEdge]] = {}
    for edge in edges:
        if not isinstance(edge, GraphEdge):
            raise TypeError("edges must be GraphEdge instances")
        edge_groups.setdefault(edge.logical_identity(), []).append(edge)

    merged_edges: list[GraphEdge] = []
    for logical_id, group in edge_groups.items():
        source, kind, target = logical_id
        if source not in index:
            raise ValueError(f"edge source not present in graph: {source.to_canonical_list()!r}")
        if target not in index:
            raise ValueError(f"edge target not present in graph: {target.to_canonical_list()!r}")
        base = group[0]
        base_attrs = base.attributes_canonical
        provenance_records = list(base.provenance)
        for other in group[1:]:
            if other.attributes_canonical != base_attrs:
                raise ValueError(
                    "conflicting GraphEdge attributes for logical edge "
                    f"{[source.to_canonical_list(), kind, target.to_canonical_list()]!r}"
                )
            provenance_records.extend(other.provenance)
        merged_edges.append(
            GraphEdge(
                source=source,
                target=target,
                kind=kind,
                attributes=_canonical_json_to_plain(base_attrs),
                provenance=_merge_provenance(provenance_records),
            )
        )

    ordered_nodes = tuple(sorted(merged_nodes, key=_node_sort_key))
    ordered_edges = tuple(sorted(merged_edges, key=lambda edge: edge.logical_sort_key()))
    return ordered_nodes, ordered_edges


@dataclass(frozen=True, slots=True)
class GovernanceGraph:
    """Immutable vendor-neutral governance graph."""

    nodes: tuple[GraphNode, ...] = field(default_factory=tuple)
    edges: tuple[GraphEdge, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Always enforce canonical invariants (direct constructor and from_parts).
        ordered_nodes, ordered_edges = _normalize_governance_graph(self.nodes, self.edges)
        object.__setattr__(self, "nodes", ordered_nodes)
        object.__setattr__(self, "edges", ordered_edges)

    @classmethod
    def from_parts(
        cls,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge] = (),
    ) -> GovernanceGraph:
        return cls(nodes=tuple(nodes), edges=tuple(edges))

    def canonical_dict_without_identity(self) -> dict[str, Any]:
        return {
            "edges": [edge.to_dict() for edge in self.edges],
            "nodes": [node.to_dict() for node in self.nodes],
        }

    def to_dict(self) -> dict[str, Any]:
        return self.canonical_dict_without_identity()
