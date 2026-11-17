"""Generate or verify deterministic public assets; no web service or credentials needed."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from governance.domain import analyze_downstream_impact
from governance.impact import (
    build_impact_result,
    identity_from_dict,
    verify_impact_result_integrity,
)
from governance.impact.contracts import validate_changes_document
from governance.impact.html import render_impact_report
from governance.integrations.dbt import load_dbt_graph
from governance.workbench.projection import project_workspace

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = (
    (
        "orders-impact",
        "Orders impact",
        "What could changing orders affect? Follow its direct and indirect consumers.",
        ROOT / "sample/offline-impact/manifest.json",
        ROOT / "sample/offline-impact/changes.json",
        "offline-demo",
    ),
    (
        "isolated-inventory",
        "Isolated inventory",
        (
            "No consumers were identified for inventory in the supplied graph. "
            "Coverage and real-world risk remain unknown."
        ),
        ROOT / "sample/offline-impact/manifest.json",
        ROOT / "sample/public-demo/inventory-changes.json",
        "offline-demo",
    ),
    (
        "multi-branch-pipeline",
        "Multi-branch data pipeline",
        (
            "Explore a synthetic orders pipeline with two processing branches "
            "and three downstream consumers."
        ),
        ROOT / "sample/public-demo/pipeline/manifest.json",
        ROOT / "sample/public-demo/pipeline/changes.json",
        "pipeline-demo",
    ),
)


def json_bytes(value):
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def generate_assets():
    assets = {}
    scenarios = []
    for scenario_id, title, description, manifest, changes_path, namespace in SCENARIOS:
        graph = load_dbt_graph(manifest, namespace=namespace)
        changes = json.loads(changes_path.read_text(encoding="utf-8"))
        validate_changes_document(changes)
        roots = tuple(identity_from_dict(n) for n in changes["changed_nodes"])
        impact = analyze_downstream_impact(graph, roots)
        result = build_impact_result(graph=graph, impact=impact)
        verify_impact_result_integrity(result)
        view = project_workspace(graph, result, synthetic=True)
        view["can_analyze"] = (
            False  # Public UI explores recorded results; it does not run analysis.
        )
        documents = {
            "graph.json": json_bytes(graph.to_dict()),
            "result.json": json_bytes(result),
            "workspace.json": json_bytes(view),
            "report.html": render_impact_report(result).encode("utf-8"),
        }
        hashes = {}
        for name, data in documents.items():
            path = f"scenarios/{scenario_id}/{name}"
            assets[path] = data
            hashes[name] = hashlib.sha256(data).hexdigest()
        scenarios.append(
            {
                "id": scenario_id,
                "title": title,
                "description": description,
                "contract": "governance-impact-result",
                "contract_version": "1",
                "result_identity": result["content_identity"],
                "files": hashes,
            }
        )
    manifest = {
        "format": "governance-public-demo-assets",
        "format_version": "1",
        "synthetic": True,
        "scenarios": scenarios,
    }
    assets["scenarios/manifest.json"] = json_bytes(manifest)
    return assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "workbench/public-demo")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify every byte and reject unexpected files without writing",
    )
    args = parser.parse_args()
    assets = generate_assets()
    metadata = ROOT / "workbench/src/generated/public-scenarios.json"
    if args.check:
        actual = {
            p.relative_to(args.output).as_posix() for p in args.output.rglob("*") if p.is_file()
        }
        if actual != set(assets):
            raise SystemExit("Public asset inventory differs from the authorized generated files")
        for name, data in assets.items():
            if (args.output / name).read_bytes() != data:
                raise SystemExit(f"Generated asset differs: {name}")
        if metadata.read_bytes() != assets["scenarios/manifest.json"]:
            raise SystemExit("Frontend scenario metadata differs")
    else:
        for name, data in assets.items():
            target = args.output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_bytes(assets["scenarios/manifest.json"])
    print(
        f"{'Verified' if args.check else 'Generated'} 3 synthetic scenarios; "
        f"{len(assets)} canonical files; zero remote writes"
    )


if __name__ == "__main__":
    main()
