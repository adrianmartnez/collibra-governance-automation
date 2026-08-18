"""CLI sync_v2 lifecycle surfaces preserve batch handles on observation failure."""

from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx
import pytest
from tests.test_collibra_import_cli import (
    _patch_scanner,
    _post_paths,
    _write_workspace,
)

from governance.cli import main
from governance.integrations.collibra.jobs import JOB_OBSERVATION_FAILURE

UUID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _patch_sync_v2_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:super-secret-password@localhost:5432/governance_demo",
    )
    monkeypatch.setenv("COLLIBRA_MODE", "live")
    monkeypatch.setenv("COLLIBRA_EXECUTION_MODE", "sync_v2")
    monkeypatch.setenv("COLLIBRA_SYNCHRONIZATION_ID", UUID_A)
    monkeypatch.setenv("COLLIBRA_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("COLLIBRA_USERNAME", "collibra-user")
    monkeypatch.setenv("COLLIBRA_PASSWORD", "collibra-secret-password")
    monkeypatch.setenv("COLLIBRA_BEARER_TOKEN", "")
    monkeypatch.setenv("COLLIBRA_CLIENT_ID", "")
    monkeypatch.setenv("COLLIBRA_CLIENT_SECRET", "")
    monkeypatch.setenv("COLLIBRA_TOKEN_URL", "")


def _patch_live_sync_http(
    monkeypatch: pytest.MonkeyPatch,
    requests: list[httpx.Request],
    *,
    batch_job_poll_status: int = 200,
) -> None:
    from governance.integrations.collibra.live import LiveCollibraAdapter

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            return httpx.Response(200, json={"results": [], "total": 0})
        if request.method == "POST" and path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if request.method == "GET" and path.endswith("/jobs/batch-1"):
            return httpx.Response(batch_job_poll_status, json={"error": "unavailable"})
        if path.endswith("/finalize/job"):
            raise AssertionError("finalize must not run on batch poll failure")
        return httpx.Response(500, json={"error": "unexpected"})

    def factory(settings, mapping_config, *, transport=None):
        del transport
        return LiveCollibraAdapter.from_settings(
            settings,
            mapping_config,
            transport=httpx.MockTransport(handler),
            sleeper=lambda _seconds: None,
        )

    monkeypatch.setattr("governance.cli.build_collibra_adapter", factory)


def _generate_sync_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    requests: list[httpx.Request],
    capsys: pytest.CaptureFixture[str],
) -> tuple[object, object]:
    _patch_sync_v2_env(monkeypatch)
    _patch_scanner(monkeypatch)

    from governance.integrations.collibra.live import LiveCollibraAdapter

    def plan_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith(("/assets", "/attributes", "/relations")):
            return httpx.Response(200, json={"results": [], "total": 0})
        return httpx.Response(500, json={"error": "unexpected"})

    def factory(settings, mapping_config, *, transport=None):
        del transport
        return LiveCollibraAdapter.from_settings(
            settings,
            mapping_config,
            transport=httpx.MockTransport(plan_handler),
            sleeper=lambda _seconds: None,
        )

    monkeypatch.setattr("governance.cli.build_collibra_adapter", factory)
    config = _write_workspace(tmp_path)
    plan_path = tmp_path / "plan.gplan"
    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(plan_path),
                "--format",
                "json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    requests.clear()
    return config, plan_path


def test_apply_sync_v2_batch_poll_failure_preserves_batch_id_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    config, plan_path = _generate_sync_plan(monkeypatch, tmp_path, requests, capsys)
    _patch_live_sync_http(monkeypatch, requests, batch_job_poll_status=503)
    code = main(
        [
            "apply",
            str(plan_path),
            "--config",
            str(config),
            "--apply",
            "--confirm-live",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["result_schema"] == "governance-sync-lifecycle-result"
    assert payload["success"] is False
    assert payload["batch_job_ids"] == ["batch-1"]
    assert payload["batch_submission_state"] == "submitted"
    assert payload["finalization_submission_state"] == "not_attempted"
    assert payload["finalization_job_id"] is None
    assert payload["batch_lifecycle"][0]["job_id"] == "batch-1"
    assert payload["error"] == JOB_OBSERVATION_FAILURE
    dumped = json.dumps(payload).lower()
    assert "collibra-secret-password" not in dumped
    assert "super-secret-password" not in dumped
    assert not any(path.endswith("/finalize/job") for path in _post_paths(requests))


def test_sync_sync_v2_batch_poll_failure_preserves_batch_id_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    _patch_sync_v2_env(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_live_sync_http(monkeypatch, requests, batch_job_poll_status=503)
    config = _write_workspace(tmp_path)
    mapping = tmp_path / "mapping.json"
    code = main(
        [
            "sync",
            "--config",
            str(config),
            "--mode",
            "live",
            "--mapping-config",
            str(mapping),
            "--apply",
            "--confirm-live",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["result_schema"] == "governance-sync-lifecycle-result"
    assert payload["batch_job_ids"] == ["batch-1"]
    assert payload["finalization_submission_state"] == "not_attempted"


def test_apply_sync_v2_batch_poll_failure_human_shows_batch_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    config, plan_path = _generate_sync_plan(monkeypatch, tmp_path, requests, capsys)
    _patch_live_sync_http(monkeypatch, requests, batch_job_poll_status=503)
    code = main(
        [
            "apply",
            str(plan_path),
            "--config",
            str(config),
            "--apply",
            "--confirm-live",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "batch_job_id_0=batch-1" in out
    assert "success=false" in out
