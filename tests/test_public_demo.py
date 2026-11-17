"""Canonical public scenarios reuse the engine and renderer without web dependencies."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
from pathlib import Path

import httpx
import pytest

from governance.impact import verify_impact_result_integrity
from governance.impact.html import render_impact_report

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "public_generator", ROOT / "sample/public-demo/generate.py"
)
assert SPEC and SPEC.loader
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Public scenario generation must remain offline")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(httpx.AsyncClient, "send", denied)
    monkeypatch.setattr("governance.config.load_settings", denied)
    monkeypatch.setattr("governance.providers.discovery.discover_provider_registrations", denied)


def test_public_assets_are_reproducible_and_inventory_is_exact():
    first = GENERATOR.generate_assets()
    assert first == GENERATOR.generate_assets()
    supplied = ROOT / "workbench/public-demo"
    assert {p.relative_to(supplied).as_posix() for p in supplied.rglob("*") if p.is_file()} == set(
        first
    )
    for name, data in first.items():
        assert (supplied / name).read_bytes() == data
    assert (ROOT / "workbench/src/generated/public-scenarios.json").read_bytes() == first[
        "scenarios/manifest.json"
    ]


@pytest.mark.parametrize(
    "scenario_id,status,direct,transitive",
    [
        ("orders-impact", "impacted", 1, 1),
        ("isolated-inventory", "clear", 0, 0),
        ("multi-branch-pipeline", "impacted", 2, 3),
    ],
)
def test_recorded_results_paths_and_reports_match_engine(scenario_id, status, direct, transitive):
    assets = GENERATOR.generate_assets()
    manifest = json.loads(assets["scenarios/manifest.json"])
    entry = next(s for s in manifest["scenarios"] if s["id"] == scenario_id)
    prefix = f"scenarios/{scenario_id}/"
    result = json.loads(assets[prefix + "result.json"])
    view = json.loads(assets[prefix + "workspace.json"])
    graph = json.loads(assets[prefix + "graph.json"])
    verify_impact_result_integrity(result)
    assert entry["contract_version"] == "1"
    assert result["content_identity"] == entry["result_identity"]
    assert result["status"] == status and result["writes_performed"] == 0
    assert len(result["impact"]["direct_nodes"]) == direct
    assert len(result["impact"]["transitive_nodes"]) == transitive
    assert view["result"] == result and view["can_analyze"] is False
    assert len(view["nodes"]) == len(graph["nodes"])
    assert len(view["edges"]) == len(graph["edges"])
    assert assets[prefix + "report.html"] == render_impact_report(result).encode("utf-8")
    for name, digest in entry["files"].items():
        assert hashlib.sha256(assets[prefix + name]).hexdigest() == digest
    identities = {n["id"] for n in view["nodes"]}
    assert all(e["source_id"] in identities and e["target_id"] in identities for e in view["edges"])
    if scenario_id == "orders-impact":
        assert result == json.loads(
            (ROOT / "sample/offline-impact/expected-result.json").read_text()
        )
        assert (
            next(n for n in view["nodes"] if n["name"] == "inventory")["impact_class"]
            == "Not reached"
        )
