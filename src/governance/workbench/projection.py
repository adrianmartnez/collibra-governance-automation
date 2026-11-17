"""Read-only UI projection shared by local API and precomputed public scenarios."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def node_id(identity: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def project_workspace(graph: Any, result: dict[str, Any] | None, *, synthetic: bool = False):
    # This is a view projection only. Distances and classifications come from the engine.
    evidence = result["impact"] if result else {}
    classes = {
        node_id(n): label
        for field, label in (
            ("changed_nodes", "Changed"),
            ("direct_nodes", "Direct"),
            ("transitive_nodes", "Transitive"),
        )
        for n in evidence.get(field, [])
    }
    distances = {node_id(p["target"]): p["distance"] for p in evidence.get("paths", [])}
    if graph is not None:
        nodes = [n.to_dict() for n in graph.nodes]
        edges = [e.to_dict() for e in graph.edges]
        coverage = "Full supplied graph"
    else:
        identities = {}
        for field in (
            "changed_nodes",
            "direct_nodes",
            "transitive_nodes",
            "governance_assets",
            "associated_contracts",
            "policy_relevant_nodes",
        ):
            for identity in evidence.get(field, []):
                identities[node_id(identity)] = identity
        edges = list(evidence.get("affected_edges", []))
        for path in evidence.get("paths", []):
            for identity in (path["root"], path["target"]):
                identities[node_id(identity)] = identity
            for step in path["steps"]:
                for identity in (step["from_node"], step["to_node"]):
                    identities[node_id(identity)] = identity
                if step["edge"] not in edges:
                    edges.append(step["edge"])
        for edge in edges:
            for identity in (edge["source"], edge["target"]):
                identities[node_id(identity)] = identity
        nodes = [
            {
                "identity": i,
                "name": i["logical_id"],
                "description": None,
                "provenance": [],
                "attributes": {},
            }
            for i in identities.values()
        ]
        coverage = "Result evidence only — full graph and unaffected assets unknown"
    projected = []
    for n in nodes:
        key = node_id(n["identity"])
        projected.append(
            {
                **n,
                "id": key,
                "impact_class": classes.get(
                    key, "Not reached" if result and graph else "Not analyzed"
                ),
                "distance": distances.get(key),
            }
        )
    return {
        "nodes": projected,
        "edges": [
            {**e, "source_id": node_id(e["source"]), "target_id": node_id(e["target"])}
            for e in edges
        ],
        "result": result,
        "synthetic": synthetic,
        "coverage": coverage,
        "can_analyze": graph is not None,
    }
