"""Deterministic downstream blast-radius analysis on GovernanceGraph.

Effective impact hops (stored edge direction may differ):

- ``depends_on``: stored derived → dependency; traverse reverse (dependency → derived)
- ``contains``: stored parent → child; traverse forward
- ``governs``: stored contract → asset; traverse forward
- unknown edge kinds: ignored
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from governance.domain.graph import (
    EDGE_KIND_CONTAINS,
    EDGE_KIND_DEPENDS_ON,
    EDGE_KIND_GOVERNS,
    NODE_KIND_COLUMN,
    NODE_KIND_CONTRACT,
    NODE_KIND_DATA_SOURCE,
    NODE_KIND_DATASET,
    NODE_KIND_TABLE,
    GovernanceGraph,
    GraphEdge,
    GraphNodeIdentity,
)
from governance.identity.canonicalize import canonical_json_bytes

TraversalMode = Literal["forward", "reverse"]

_POLICY_RELEVANT_KINDS = frozenset(
    {
        NODE_KIND_DATA_SOURCE,
        NODE_KIND_DATASET,
        NODE_KIND_TABLE,
        NODE_KIND_COLUMN,
    }
)


@dataclass(frozen=True, slots=True)
class _EffectiveHop:
    from_node: GraphNodeIdentity
    to_node: GraphNodeIdentity
    edge: GraphEdge
    traversal: TraversalMode


def _node_sort_key(identity: GraphNodeIdentity) -> bytes:
    return identity.canonical_bytes()


def _hop_neighbor_sort_key(hop: _EffectiveHop) -> tuple[bytes, bytes, str]:
    return (hop.to_node.canonical_bytes(), hop.edge.logical_sort_key(), hop.traversal)


def _path_sort_key(path: ImpactPath) -> bytes:
    return canonical_json_bytes(
        [
            path.root.to_canonical_list(),
            [
                [
                    step.from_node.to_canonical_list(),
                    step.to_node.to_canonical_list(),
                    step.edge.source.to_canonical_list(),
                    step.edge.kind,
                    step.edge.target.to_canonical_list(),
                    step.traversal,
                ]
                for step in path.steps
            ],
        ]
    )


def _validate_impact_step_direction(
    from_node: GraphNodeIdentity,
    to_node: GraphNodeIdentity,
    edge: GraphEdge,
    traversal: TraversalMode,
) -> None:
    if edge.kind == EDGE_KIND_DEPENDS_ON:
        if traversal != "reverse" or from_node != edge.target or to_node != edge.source:
            raise ValueError("impact step traversal incompatible with edge kind")
        return
    if edge.kind in (EDGE_KIND_CONTAINS, EDGE_KIND_GOVERNS):
        if traversal != "forward" or from_node != edge.source or to_node != edge.target:
            raise ValueError("impact step traversal incompatible with edge kind")
        return
    raise ValueError("impact step traversal incompatible with edge kind")


@dataclass(frozen=True, slots=True)
class ImpactStep:
    """One effective hop in a blast-radius explanation path."""

    from_node: GraphNodeIdentity
    to_node: GraphNodeIdentity
    edge: GraphEdge
    traversal: TraversalMode

    def __post_init__(self) -> None:
        if not isinstance(self.from_node, GraphNodeIdentity):
            raise TypeError("from_node must be GraphNodeIdentity")
        if not isinstance(self.to_node, GraphNodeIdentity):
            raise TypeError("to_node must be GraphNodeIdentity")
        if not isinstance(self.edge, GraphEdge):
            raise TypeError("edge must be GraphEdge")
        if self.traversal not in ("forward", "reverse"):
            raise ValueError("traversal must be 'forward' or 'reverse'")
        _validate_impact_step_direction(self.from_node, self.to_node, self.edge, self.traversal)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge": self.edge.to_dict(),
            "from_node": self.from_node.to_dict(),
            "to_node": self.to_node.to_dict(),
            "traversal": self.traversal,
        }


@dataclass(frozen=True, slots=True)
class ImpactPath:
    """Canonical shortest explanation path from a changed root to an affected node."""

    root: GraphNodeIdentity
    target: GraphNodeIdentity
    steps: tuple[ImpactStep, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.root, GraphNodeIdentity):
            raise TypeError("root must be GraphNodeIdentity")
        if not isinstance(self.target, GraphNodeIdentity):
            raise TypeError("target must be GraphNodeIdentity")
        if not isinstance(self.steps, tuple):
            object.__setattr__(self, "steps", tuple(self.steps))
        if not self.steps:
            raise ValueError("impact path requires at least one step")
        for step in self.steps:
            if not isinstance(step, ImpactStep):
                raise TypeError("steps must be ImpactStep instances")
        if self.steps[0].from_node != self.root:
            raise ValueError("impact path must start at root")
        if self.steps[-1].to_node != self.target:
            raise ValueError("impact path must end at target")
        for index in range(len(self.steps) - 1):
            if self.steps[index].to_node != self.steps[index + 1].from_node:
                raise ValueError("impact path steps must be contiguous")

    @property
    def distance(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "distance": self.distance,
            "root": self.root.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "target": self.target.to_dict(),
        }


def _normalize_identity_tuple(
    values: Sequence[GraphNodeIdentity],
    field_name: str,
) -> tuple[GraphNodeIdentity, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence of GraphNodeIdentity")
    unique: dict[GraphNodeIdentity, None] = {}
    for value in values:
        if not isinstance(value, GraphNodeIdentity):
            raise TypeError(f"{field_name} entries must be GraphNodeIdentity")
        unique[value] = None
    return tuple(sorted(unique.keys(), key=_node_sort_key))


def _normalize_affected_edges(values: Sequence[GraphEdge]) -> tuple[GraphEdge, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise TypeError("affected_edges must be a sequence of GraphEdge")
    groups: dict[tuple[GraphNodeIdentity, str, GraphNodeIdentity], list[GraphEdge]] = {}
    for edge in values:
        if not isinstance(edge, GraphEdge):
            raise TypeError("affected_edges entries must be GraphEdge")
        groups.setdefault(edge.logical_identity(), []).append(edge)

    merged: list[GraphEdge] = []
    for key, group in groups.items():
        base = group[0]
        for other in group[1:]:
            if other.attributes_canonical != base.attributes_canonical:
                source, kind, target = key
                raise ValueError(
                    "conflicting GraphEdge attributes for logical edge "
                    f"{[source.to_canonical_list(), kind, target.to_canonical_list()]!r}"
                )
        provenance = [record for edge in group for record in edge.provenance]
        merged.append(
            GraphEdge(
                source=base.source,
                target=base.target,
                kind=base.kind,
                attributes=base.attributes,
                provenance=tuple(provenance),
            )
        )
    return tuple(sorted(merged, key=lambda edge: edge.logical_sort_key()))


def _normalize_paths(values: Sequence[ImpactPath]) -> tuple[ImpactPath, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise TypeError("paths must be a sequence of ImpactPath")
    by_target: dict[GraphNodeIdentity, ImpactPath] = {}
    for path in values:
        if not isinstance(path, ImpactPath):
            raise TypeError("paths entries must be ImpactPath")
        if path.target in by_target:
            raise ValueError(f"duplicate impact path target: {path.target.to_canonical_list()!r}")
        by_target[path.target] = path
    return tuple(sorted(by_target.values(), key=lambda path: path.target.canonical_bytes()))


@dataclass(frozen=True, slots=True)
class GovernanceImpactResult:
    """Deterministic downstream blast-radius analysis result."""

    changed_nodes: tuple[GraphNodeIdentity, ...]
    direct_nodes: tuple[GraphNodeIdentity, ...]
    transitive_nodes: tuple[GraphNodeIdentity, ...]
    affected_edges: tuple[GraphEdge, ...]
    paths: tuple[ImpactPath, ...]
    associated_contracts: tuple[GraphNodeIdentity, ...]
    governance_assets: tuple[GraphNodeIdentity, ...]
    policy_relevant_nodes: tuple[GraphNodeIdentity, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "changed_nodes", _normalize_identity_tuple(self.changed_nodes, "changed_nodes")
        )
        object.__setattr__(
            self, "direct_nodes", _normalize_identity_tuple(self.direct_nodes, "direct_nodes")
        )
        object.__setattr__(
            self,
            "transitive_nodes",
            _normalize_identity_tuple(self.transitive_nodes, "transitive_nodes"),
        )
        object.__setattr__(self, "affected_edges", _normalize_affected_edges(self.affected_edges))
        object.__setattr__(self, "paths", _normalize_paths(self.paths))
        object.__setattr__(
            self,
            "associated_contracts",
            _normalize_identity_tuple(self.associated_contracts, "associated_contracts"),
        )
        object.__setattr__(
            self,
            "governance_assets",
            _normalize_identity_tuple(self.governance_assets, "governance_assets"),
        )
        object.__setattr__(
            self,
            "policy_relevant_nodes",
            _normalize_identity_tuple(self.policy_relevant_nodes, "policy_relevant_nodes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "affected_edges": [edge.to_dict() for edge in self.affected_edges],
            "associated_contracts": [node.to_dict() for node in self.associated_contracts],
            "changed_nodes": [node.to_dict() for node in self.changed_nodes],
            "direct_nodes": [node.to_dict() for node in self.direct_nodes],
            "governance_assets": [node.to_dict() for node in self.governance_assets],
            "paths": [path.to_dict() for path in self.paths],
            "policy_relevant_nodes": [node.to_dict() for node in self.policy_relevant_nodes],
            "transitive_nodes": [node.to_dict() for node in self.transitive_nodes],
        }


def _build_effective_adjacency(
    graph: GovernanceGraph,
) -> dict[GraphNodeIdentity, tuple[_EffectiveHop, ...]]:
    buckets: dict[GraphNodeIdentity, list[_EffectiveHop]] = {}
    for edge in graph.edges:
        if edge.kind == EDGE_KIND_DEPENDS_ON:
            hop = _EffectiveHop(
                from_node=edge.target,
                to_node=edge.source,
                edge=edge,
                traversal="reverse",
            )
        elif edge.kind in (EDGE_KIND_CONTAINS, EDGE_KIND_GOVERNS):
            hop = _EffectiveHop(
                from_node=edge.source,
                to_node=edge.target,
                edge=edge,
                traversal="forward",
            )
        else:
            continue
        buckets.setdefault(hop.from_node, []).append(hop)

    return {node: tuple(sorted(hops, key=_hop_neighbor_sort_key)) for node, hops in buckets.items()}


def _normalize_changed_roots(
    graph: GovernanceGraph,
    changed_nodes: Sequence[GraphNodeIdentity],
) -> tuple[GraphNodeIdentity, ...]:
    if not isinstance(graph, GovernanceGraph):
        raise TypeError("graph must be GovernanceGraph")
    if not isinstance(changed_nodes, Sequence) or isinstance(changed_nodes, (str, bytes)):
        raise TypeError("changed_nodes must be a sequence of GraphNodeIdentity")

    unique: dict[GraphNodeIdentity, None] = {}
    for node in changed_nodes:
        if not isinstance(node, GraphNodeIdentity):
            raise TypeError("changed_nodes entries must be GraphNodeIdentity")
        unique[node] = None

    if not unique:
        raise ValueError("changed_nodes must not be empty")

    present = {graph_node.identity for graph_node in graph.nodes}
    for node in unique:
        if node not in present:
            raise ValueError(f"changed node not present in graph: {node.to_canonical_list()!r}")

    return tuple(sorted(unique.keys(), key=_node_sort_key))


def _make_step(hop: _EffectiveHop) -> ImpactStep:
    return ImpactStep(
        from_node=hop.from_node,
        to_node=hop.to_node,
        edge=hop.edge,
        traversal=hop.traversal,
    )


def _extend_path(
    parent: GraphNodeIdentity,
    parent_path: ImpactPath | None,
    hop: _EffectiveHop,
) -> ImpactPath:
    step = _make_step(hop)
    if parent_path is None:
        return ImpactPath(root=parent, target=hop.to_node, steps=(step,))
    return ImpactPath(
        root=parent_path.root,
        target=hop.to_node,
        steps=(*parent_path.steps, step),
    )


def _collect_affected_edges(
    adjacency: dict[GraphNodeIdentity, tuple[_EffectiveHop, ...]],
    closure: set[GraphNodeIdentity],
) -> tuple[GraphEdge, ...]:
    edges: dict[tuple[GraphNodeIdentity, str, GraphNodeIdentity], GraphEdge] = {}
    for from_node in closure:
        for hop in adjacency.get(from_node, ()):
            if hop.to_node in closure:
                edges[hop.edge.logical_identity()] = hop.edge
    return tuple(sorted(edges.values(), key=lambda edge: edge.logical_sort_key()))


def _associate_governance_assets(
    graph: GovernanceGraph,
    impact_core: set[GraphNodeIdentity],
) -> tuple[GraphNodeIdentity, ...]:
    present = {node.identity for node in graph.nodes}
    assets: dict[GraphNodeIdentity, None] = {}
    for node in impact_core:
        if node.kind == NODE_KIND_CONTRACT:
            continue
        assets[node] = None
        for ancestor in node.ancestors():
            if ancestor in present and ancestor.kind != NODE_KIND_CONTRACT:
                assets[ancestor] = None
    return tuple(sorted(assets.keys(), key=_node_sort_key))


def _associate_contracts(
    graph: GovernanceGraph,
    impact_core: set[GraphNodeIdentity],
) -> tuple[GraphNodeIdentity, ...]:
    contracts: dict[GraphNodeIdentity, None] = {}
    for node in impact_core:
        if node.kind == NODE_KIND_CONTRACT:
            contracts[node] = None

    governs_by_target: dict[GraphNodeIdentity, list[GraphNodeIdentity]] = {}
    present = {graph_node.identity for graph_node in graph.nodes}
    for edge in graph.edges:
        if edge.kind != EDGE_KIND_GOVERNS:
            continue
        if edge.source.kind != NODE_KIND_CONTRACT:
            continue
        if edge.source not in present:
            continue
        governs_by_target.setdefault(edge.target, []).append(edge.source)

    for node in impact_core:
        for identity in (node, *node.ancestors()):
            for contract in governs_by_target.get(identity, ()):
                contracts[contract] = None

    return tuple(sorted(contracts.keys(), key=_node_sort_key))


def _policy_relevant_nodes(
    governance_assets: Sequence[GraphNodeIdentity],
) -> tuple[GraphNodeIdentity, ...]:
    relevant = [node for node in governance_assets if node.kind in _POLICY_RELEVANT_KINDS]
    return tuple(sorted(relevant, key=_node_sort_key))


def analyze_downstream_impact(
    graph: GovernanceGraph,
    changed_nodes: Sequence[GraphNodeIdentity],
) -> GovernanceImpactResult:
    """Compute deterministic downstream blast radius for changed graph nodes.

    Zero I/O. Does not mutate ``graph``. Does not evaluate policies.
    """
    roots = _normalize_changed_roots(graph, changed_nodes)
    root_set = set(roots)
    adjacency = _build_effective_adjacency(graph)

    distance: dict[GraphNodeIdentity, int] = {root: 0 for root in roots}
    best_path: dict[GraphNodeIdentity, ImpactPath] = {}

    frontier = list(roots)
    while frontier:
        next_candidates: dict[GraphNodeIdentity, ImpactPath] = {}
        for node in sorted(frontier, key=_node_sort_key):
            parent_path = best_path.get(node)
            for hop in adjacency.get(node, ()):
                target = hop.to_node
                if target in root_set:
                    continue
                candidate = _extend_path(node, parent_path, hop)
                new_distance = distance[node] + 1
                existing = distance.get(target)
                if existing is not None and new_distance > existing:
                    continue
                if existing is not None and new_distance == existing:
                    current_best = best_path[target]
                    if _path_sort_key(candidate) >= _path_sort_key(current_best):
                        continue
                    best_path[target] = candidate
                    # Equal-distance path improvement within the same discovery layer
                    # does not reopen expansion; prefixes are finalized per layer.
                    continue
                # First visit or strictly shorter path (should only be first visit in BFS).
                distance[target] = new_distance
                best_path[target] = candidate
                next_candidates[target] = candidate

        # Within this layer's discoveries, prefer canonical path among equal-distance arrivals.
        # Re-scan parents once more so tie-breaks are independent of parent scan order.
        for node in sorted(frontier, key=_node_sort_key):
            parent_path = best_path.get(node)
            for hop in adjacency.get(node, ()):
                target = hop.to_node
                if target not in next_candidates:
                    continue
                candidate = _extend_path(node, parent_path, hop)
                if distance.get(target) != distance[node] + 1:
                    continue
                if _path_sort_key(candidate) < _path_sort_key(best_path[target]):
                    best_path[target] = candidate
                    next_candidates[target] = candidate

        frontier = sorted(next_candidates.keys(), key=_node_sort_key)

    direct_nodes = tuple(
        sorted(
            (node for node, dist in distance.items() if dist == 1),
            key=_node_sort_key,
        )
    )
    transitive_nodes = tuple(
        sorted(
            (node for node, dist in distance.items() if dist >= 2),
            key=_node_sort_key,
        )
    )
    impact_core = set(roots) | set(direct_nodes) | set(transitive_nodes)
    affected_edges = _collect_affected_edges(adjacency, impact_core)
    paths = tuple(
        sorted(
            (best_path[node] for node in (*direct_nodes, *transitive_nodes)),
            key=lambda path: path.target.canonical_bytes(),
        )
    )

    governance_assets = _associate_governance_assets(graph, impact_core)
    associated_contracts = _associate_contracts(graph, impact_core)
    policy_relevant = _policy_relevant_nodes(governance_assets)

    return GovernanceImpactResult(
        changed_nodes=roots,
        direct_nodes=direct_nodes,
        transitive_nodes=transitive_nodes,
        affected_edges=affected_edges,
        paths=paths,
        associated_contracts=associated_contracts,
        governance_assets=governance_assets,
        policy_relevant_nodes=policy_relevant,
    )
