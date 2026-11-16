"""Offline example, evidence, canonical contract and existing CLI regression checks."""

from __future__ import annotations

import os
import runpy
import socket
from pathlib import Path

import httpx
import psycopg
import pytest

from governance.cli import main
from governance.impact import canonical_impact_json, load_impact_result
from governance.orchestration.registry import build_provider_registry

DEMO = Path(__file__).resolve().parents[1] / "sample" / "offline-impact"


@pytest.fixture(autouse=True)
def deny_external_access(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Offline demo attempted external access")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(httpx.AsyncClient, "send", denied)
    monkeypatch.setattr(psycopg, "connect", denied)
    monkeypatch.setattr(psycopg.Connection, "connect", denied)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", denied)
    for name in tuple(os.environ):
        if name.startswith(("COLLIBRA_", "PG", "GOVERNANCE_")) or name == "DATABASE_URL":
            monkeypatch.delenv(name)


def test_demo_is_canonical_and_reproducible(tmp_path, monkeypatch):
    run = runpy.run_path(str(DEMO / "run.py"))["run"]
    inputs = {path: path.read_bytes() for path in DEMO.iterdir() if path.is_file()}
    monkeypatch.chdir(tmp_path)
    for name in ("first.json", "second.json"):
        run(Path(name))
        assert Path(name).read_bytes() == (DEMO / "expected-result.json").read_bytes()
    assert set(path.name for path in tmp_path.iterdir()) == {"first.json", "second.json"}
    assert all(path.read_bytes() == content for path, content in inputs.items())
    result = load_impact_result(tmp_path / "first.json")
    assert canonical_impact_json(result).encode() == (tmp_path / "first.json").read_bytes()
    assert result["result_schema"] == "governance-impact-result"
    assert result["result_version"] == "1"
    assert result["writes_performed"] == 0
    assert result["status"] == "impacted"
    assert result["impact_detected"] is True
    assert result["policy_identity"] is None
    assert result["affected_policies"] == []
    impact = result["impact"]
    assert [n["logical_id"] for n in impact["changed_nodes"]] == ["orders"]
    assert [n["logical_id"] for n in impact["direct_nodes"]] == ["order_totals"]
    assert [n["logical_id"] for n in impact["transitive_nodes"]] == ["revenue_report"]
    paths = {p["target"]["logical_id"]: p for p in impact["paths"]}
    assert set(paths) == {"order_totals", "revenue_report"}
    assert len(impact["affected_edges"]) == 2
    for target, chain in (
        ("order_totals", ["order_totals"]),
        ("revenue_report", ["order_totals", "revenue_report"]),
    ):
        path = paths[target]
        assert path["root"] == impact["changed_nodes"][0]
        assert path["distance"] == len(chain)
        assert [s["to_node"]["logical_id"] for s in path["steps"]] == chain
        previous = "orders"
        for step, child in zip(path["steps"], chain, strict=True):
            assert step["traversal"] == "reverse"
            assert step["from_node"]["logical_id"] == previous
            assert step["edge"]["kind"] == "depends_on"
            assert step["edge"]["source"] == step["to_node"]
            assert step["edge"]["target"] == step["from_node"]
            assert step["edge"]["provenance"] == [
                {
                    "observation_mode": "declared",
                    "provider_type": "dbt",
                    "source_ref": f"model.demo.{child}",
                    "source_version": "1.10.0",
                }
            ]
            previous = child


def test_existing_cli_produces_same_artifact_offline(tmp_path, monkeypatch, capsys):
    # Model a clean installation without unrelated externally installed providers.
    registry = build_provider_registry(discover_external=False)
    monkeypatch.setattr("governance.cli.build_provider_registry", lambda **kwargs: registry)
    output = tmp_path / "result.json"
    assert (
        main(
            [
                "impact",
                "--namespace",
                "offline-demo",
                "--changes",
                str(DEMO / "changes.json"),
                "--dbt-manifest",
                str(DEMO / "manifest.json"),
                "--output",
                str(output),
                "--format",
                "json",
            ]
        )
        == 6
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.encode() == output.read_bytes()
    assert output.read_bytes() == (DEMO / "expected-result.json").read_bytes()
    assert load_impact_result(output)["writes_performed"] == 0


def test_documented_script_entrypoint(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(DEMO / "run.py"), run_name="__main__")
    assert (tmp_path / "offline-impact-result.json").read_bytes() == (
        DEMO / "expected-result.json"
    ).read_bytes()
    assert "remote writes: 0" in capsys.readouterr().out
