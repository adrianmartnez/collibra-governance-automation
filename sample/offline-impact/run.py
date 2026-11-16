"""Generate the offline demo artifact using existing local impact APIs."""
from pathlib import Path

from governance.domain import analyze_downstream_impact
from governance.impact import build_impact_result, load_impact_changes, write_impact_result
from governance.integrations.dbt import load_dbt_graph


def run(output: Path) -> None:
    fixture = Path(__file__).resolve().parent
    graph = load_dbt_graph(fixture / "manifest.json", namespace="offline-demo")
    changes = load_impact_changes(fixture / "changes.json", expected_namespace="offline-demo")
    result = build_impact_result(
        graph=graph, impact=analyze_downstream_impact(graph, changes)
    )
    write_impact_result(output, result)


if __name__ == "__main__":
    run(Path("offline-impact-result.json"))
    print("Created offline-impact-result.json (governance-impact-result v1; remote writes: 0)")
