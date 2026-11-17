"""Focused optional workbench boundary and real-engine integration tests."""

from __future__ import annotations

import copy
import importlib
import json
import socket
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="Install the optional workbench extra to test the local API.")

from fastapi.testclient import TestClient

from governance.github_ci.review import build_review_result
from governance.identity.hashing import ContentIdentity
from governance.impact.html import render_impact_report
from governance.integrations.collibra.models import (
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
)
from governance.plans.models import SavedGovernancePlan
from governance.workbench.api import MAX_BYTES, RESOURCES, create_app

HEADERS = {"X-Workbench-Request": "1"}
DEMO = Path(__file__).resolve().parents[1] / "sample/offline-impact"


@pytest.fixture
def client(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Workbench must not connect to services or discover providers")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr("governance.providers.discovery.discover_provider_registrations", forbidden)
    monkeypatch.setattr("governance.config.load_settings", forbidden)
    monkeypatch.setattr("psycopg.connect", forbidden)
    monkeypatch.setattr("psycopg.Connection.connect", forbidden)
    monkeypatch.setattr(
        importlib.import_module("governance.providers.builtins.collibra"),
        "build_collibra_adapter",
        forbidden,
    )
    with TestClient(create_app(), base_url="http://127.0.0.1:8765", headers=HEADERS) as connection:
        yield connection


def load_demo(client):
    response = client.post("/api/demo", json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_demo_and_export_match_existing_engine(client):
    first = load_demo(client)
    assert first == load_demo(client)
    assert first["synthetic"] is True and first["can_analyze"] is True
    assert len(first["nodes"]) == 6
    assert first["result"] == json.loads((DEMO / "expected-result.json").read_text())
    tables = {n["name"]: n for n in first["nodes"] if n["identity"]["kind"] == "table"}
    assert {k: v["impact_class"] for k, v in tables.items()} == {
        "orders": "Changed",
        "order_totals": "Direct",
        "revenue_report": "Transitive",
        "inventory": "Not reached",
    }
    assert tables["revenue_report"]["distance"] == 2
    dependencies = [e for e in first["edges"] if e["kind"] == "depends_on"]
    assert {(e["source"]["logical_id"], e["target"]["logical_id"]) for e in dependencies} == {
        ("order_totals", "orders"),
        ("revenue_report", "order_totals"),
    }
    response = client.post("/api/report", json=first["result"])
    assert response.status_code == 200
    assert response.text == render_impact_report(first["result"])
    assert "attachment" in response.headers["content-disposition"]
    assert first["result"]["writes_performed"] == 0


@pytest.mark.parametrize(
    "root,direct,transitive,status",
    [
        ("orders", 1, 1, "impacted"),
        ("order_totals", 1, 0, "impacted"),
        ("inventory", 0, 0, "clear"),
        ("revenue_report", 0, 0, "clear"),
    ],
)
def test_selected_root_runs_real_engine(client, root, direct, transitive, status):
    data = load_demo(client)
    identity = next(n["identity"] for n in data["nodes"] if n["name"] == root)
    changes = {
        "changes_schema": "governance-impact-changes",
        "changes_version": "1",
        "changed_nodes": [identity],
    }
    response = client.post(
        "/api/impact",
        json={"manifest": data["manifest"], "namespace": "offline-demo", "changes": changes},
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["status"] == status
    assert len(result["impact"]["direct_nodes"]) == direct
    assert len(result["impact"]["transitive_nodes"]) == transitive
    assert result["writes_performed"] == 0


def test_manifest_import_and_result_only_coverage(client):
    data = load_demo(client)
    full = client.post(
        "/api/graph", json={"manifest": data["manifest"], "namespace": "offline-demo"}
    )
    assert full.status_code == 200 and full.json()["result"] is None
    assert all(n["impact_class"] == "Not analyzed" for n in full.json()["nodes"])
    partial = client.post("/api/result", json=data["result"])
    assert partial.status_code == 200
    assert partial.json()["can_analyze"] is False
    assert "evidence only" in partial.json()["coverage"]
    assert "inventory" not in [n["name"] for n in partial.json()["nodes"]]


@pytest.mark.parametrize("version", ["1", "2"])
def test_saved_plan_is_inspected_never_applied(client, version):
    identity = ContentIdentity("sha256", "1", "a" * 64)
    action = SyncAction(
        SyncActionType.UNCHANGED,
        SyncObjectKind.ASSET,
        local_id="demo/orders",
        reason="Existing unchanged object",
    )
    plan = SavedGovernancePlan(
        sync_plan=SyncPlan(actions=(action,)),
        config_identity=identity,
        snapshot_identity=identity,
        policy_identity=identity,
        mapping_identity=identity,
        target_context={"provider": "collibra", "mode": "mock"},
        target_context_identity=identity,
        remote_state_identity=identity,
        plan_version=version,
    )
    document = plan.to_dict()
    response = client.post("/api/plan", json=document)
    assert response.status_code == 200, response.text
    assert response.json() == document
    document["actions"][0]["reason"] = "tampered"
    assert client.post("/api/plan", json=document).status_code == 422


def test_review_validation_and_absent_lane_are_preserved(client):
    document = build_review_result()
    response = client.post("/api/review", json=document)
    assert response.status_code == 200, response.text
    assert response.json() == document
    assert response.json()["conflicts"]["status"] == "not_run"
    document["status"] = "blocked"
    assert client.post("/api/review", json=document).status_code == 422


@pytest.mark.parametrize("endpoint", ["result", "report", "plan", "review"])
def test_rejects_invalid_artifacts(client, endpoint):
    assert client.post(f"/api/{endpoint}", json={"path": "../../.env"}).status_code == 422
    assert (
        client.post(
            f"/api/{endpoint}", content=b"{", headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )


def test_result_integrity_and_versions(client):
    document = load_demo(client)["result"]
    document["result_version"] = "9"
    assert client.post("/api/result", json=document).status_code == 422
    document["result_version"] = "1"
    document["status"] = "clear"
    assert client.post("/api/report", json=document).status_code == 422


def test_local_security_boundary(client):
    assert client.get("/api/health").json()["writes_performed"] == 0
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    response = client.post("/api/demo", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers
    assert (
        client.post("/api/demo", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )
    assert (
        client.options("/api/demo", headers={"Origin": "https://evil.example"}).status_code == 403
    )
    assert client.post("/api/demo", content="path=../../.env").status_code == 415
    assert client.delete("/api/demo").status_code == 405
    with TestClient(create_app(), base_url="http://127.0.0.1:8765") as no_header:
        assert no_header.post("/api/demo", json={}).status_code == 403
    for path in ("apply", "sync", "execute", "files", "scan"):
        assert client.post(f"/api/{path}", json={}).status_code == 404
    assert client.get("/api/../../.env").status_code == 404


def test_size_and_nesting_limits(client):
    assert (
        client.post(
            "/api/demo",
            content=b" " * (MAX_BYTES + 1),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )

    def chunks():
        yield b" " * MAX_BYTES
        yield b"x"

    assert (
        client.post(
            "/api/demo", content=chunks(), headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/demo",
            content='{"a":' * 45 + "0" + "}" * 45,
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
    data = load_demo(client)
    manifest = copy.deepcopy(data["manifest"])
    base = next(iter(manifest["nodes"].values()))
    for i in range(201):
        uid = f"model.demo.extra{i}"
        manifest["nodes"][uid] = {
            **base,
            "unique_id": uid,
            "name": f"extra{i}",
            "alias": f"extra{i}",
            "fqn": ["demo", f"extra{i}"],
        }
    assert (
        client.post(
            "/api/graph", json={"manifest": manifest, "namespace": "offline-demo"}
        ).status_code
        == 413
    )


def test_missing_demo_is_explicit():
    with TestClient(
        create_app(Path("missing-workbench-demo")),
        base_url="http://127.0.0.1:8765",
        headers=HEADERS,
    ) as connection:
        assert connection.post("/api/demo", json={}).status_code == 503


def test_packaged_demo_is_canonical():
    for name in ("manifest.json", "changes.json"):
        assert RESOURCES.joinpath("demo", name).read_bytes() == (DEMO / name).read_bytes()


def test_packaged_frontend_and_assets_are_served(client, monkeypatch, tmp_path):
    import re

    monkeypatch.chdir(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', response.text)
    assert len(assets) >= 2
    for asset in assets:
        assert client.get(asset).status_code == 200
    assert client.get("/resources/demo/manifest.json").status_code == 404
    assert load_demo(client)["result"]["writes_performed"] == 0
