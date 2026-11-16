"""Offline, read-only HTML projection of the existing impact result v1 contract."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from governance.impact.contracts import load_impact_result, verify_impact_result_integrity


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _json(value: Any) -> str:
    return (
        "<pre>" + _text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True)) + "</pre>"
    )


def _node(node: dict[str, Any]) -> str:
    # Include namespace and kind at every level to preserve identity boundaries.
    chain = []
    current = node
    while current is not None:
        chain.append(
            f"{_text(current['namespace'])}:{_text(current['kind'])}:{_text(current['logical_id'])}"
        )
        current = current["parent"]
    return " / ".join(reversed(chain))


def _nodes(title: str, nodes: list[dict[str, Any]]) -> str:
    body = "<ul>" + "".join(f"<li>{_node(n)}</li>" for n in nodes) + "</ul>"
    return (
        f"<section><h2>{title} ({len(nodes)})</h2>"
        + (body if nodes else "<p>None recorded in this artifact.</p>")
        + "</section>"
    )


def _edge(edge: dict[str, Any]) -> str:
    provenance = (
        _json(edge["provenance"])
        if edge["provenance"]
        else "<p>Insufficient evidence: relationship provenance is not recorded.</p>"
    )
    return (
        f"<p>Stored relationship: {_node(edge['source'])} &rarr; "
        f"{_node(edge['target'])}; kind: {_text(edge['kind'])}</p>"
        "<h4>Relationship provenance</h4>"
        + provenance
        + "<h4>Relationship attributes</h4>"
        + _json(edge["attributes"])
    )


def render_impact_report(result: dict[str, Any]) -> str:
    """Return deterministic HTML; validate schema and integrity, never infer impact.

    This function performs no I/O and does not modify the supplied artifact.
    Contract/integrity exceptions propagate unchanged to the caller.
    """
    verify_impact_result_integrity(result)
    impact = result["impact"]
    label = (
        "Impact identified"
        if result["status"] == "impacted"
        else "No impact identified in the supplied graph"
    )
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'\">",
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Governance impact report</title>",
        "<style>body{font:1rem system-ui,sans-serif;max-width:75rem;margin:2rem auto;"
        "padding:0 1rem;line-height:1.5;color:#17212b;background:#fff}"
        "section{border-top:1px solid #ccd3da;margin-top:1.5rem}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f7;padding:1rem}"
        "li,p{overflow-wrap:anywhere}h3{margin-top:1.5rem}</style></head><body><main>",
        "<h1>Governance impact report</h1>",
        f"<p><strong>{label}</strong></p>",
        f"<p>Artifact status: {_text(result['status'])}; impact_detected: "
        f"{_text(result['impact_detected'])}; writes_performed: {_text(result['writes_performed'])}"
        "</p>",
        "<p>Read-only presentation of governance-impact-result v1. "
        "No analysis decisions are recomputed.</p>",
    ]
    for title, field in (
        ("Changed assets", "changed_nodes"),
        ("Direct impacts", "direct_nodes"),
        ("Transitive impacts", "transitive_nodes"),
    ):
        parts.append(_nodes(title, impact[field]))
    parts.append("<section><h2>Recorded dependencies</h2>")
    parts.extend(_edge(edge) for edge in impact["affected_edges"])
    if not impact["affected_edges"]:
        parts.append("<p>No affected relationships recorded.</p>")
    parts.append("</section><section><h2>Recorded impact paths</h2>")
    for path in impact["paths"]:
        parts.append(
            f"<h3>{_node(path['root'])} &rarr; {_node(path['target'])}</h3>"
            f"<p>Recorded distance: {_text(path['distance'])}</p><ol>"
        )
        for step in path["steps"]:
            parts.append(
                f"<li><p>Traversal: {_text(step['traversal'])}; "
                f"{_node(step['from_node'])} &rarr; {_node(step['to_node'])}</p>"
                + _edge(step["edge"])
                + "</li>"
            )
        parts.append("</ol>")
    if not impact["paths"]:
        parts.append("<p>No paths recorded; no path evidence can be displayed.</p>")
    parts.append("</section>")
    for title, field in (
        ("Associated contracts", "associated_contracts"),
        ("Governance assets", "governance_assets"),
        ("Policy matching context (not additional downstream impacts)", "policy_relevant_nodes"),
    ):
        parts.append(_nodes(title, impact[field]))
    parts.append("<section><h2>Affected policies (not compliance verdicts)</h2>")
    parts.append(_json(result["affected_policies"]))
    parts.append("</section><section><h2>Limitations and missing evidence</h2>")
    if not impact["changed_nodes"]:
        parts.append("<p>Insufficient evidence: no changed assets recorded.</p>")
    targets = {json.dumps(p["target"], sort_keys=True) for p in impact["paths"]}
    if any(
        json.dumps(n, sort_keys=True) not in targets
        for n in impact["direct_nodes"] + impact["transitive_nodes"]
    ):
        parts.append("<p>Insufficient evidence: some listed impacts have no recorded path.</p>")
    if result["policy_identity"] is None:
        parts.append("<p>No policy set identity recorded; policy compliance is unknown.</p>")
    parts.append(
        "<p>Contract v1 has no coverage or completeness verdict and no insufficient-evidence "
        "analysis status. Missing evidence is reported separately; the artifact status above "
        "is preserved. Empty lists do not establish complete coverage or production safety.</p>"
        "<p>Only recorded relationships and paths are shown. Unrecorded dependencies, "
        "external consumers, runtime breakage, data values and business severity are unknown. "
        "Declared, observed and derived provenance are displayed as recorded, not upgraded "
        "to stronger evidence. No commercial tenant validation is claimed.</p></section>"
        "<section><h2>Artifact identities</h2>"
    )
    for field in ("content_identity", "graph_identity", "policy_identity"):
        parts.append(f"<h3>{field}</h3>" + _json(result[field]))
    parts.append("</section></main></body></html>\n")
    return "".join(parts)


def render_impact_report_from_file(path: str | Path) -> str:
    """Read through the existing v1 loader; return HTML without writing any files."""
    return render_impact_report(load_impact_result(path))
