"""CLI import_v2 apply/sync surfaces. core_rest/mock contracts stay in test_cli."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from governance.cli import main
from governance.domain import (
    Column,
    Database,
    DataSource,
    GovernanceModel,
    Ownership,
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_schema_id,
    make_table_id,
)
from governance.integrations.collibra.live import LiveCollibraAdapter

CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


def _tiny_model() -> GovernanceModel:
    source = "governance-demo"
    database = "governance_demo"
    schema = "commerce"
    table_id = make_table_id(source, database, schema, "orders")
    column_id = make_column_id(source, database, schema, "orders", "order_id")
    return GovernanceModel(
        data_sources=(
            DataSource(
                id=make_datasource_id(source),
                name=source,
                system_type="postgresql",
                databases=(
                    Database(
                        id=make_database_id(source, database),
                        name=database,
                        datasource_id=make_datasource_id(source),
                        ownership=Ownership(owner_name="postgres"),
                        schemas=(
                            Schema(
                                id=make_schema_id(source, database, schema),
                                name=schema,
                                database_id=make_database_id(source, database),
                                ownership=Ownership(owner_name="governance_owner"),
                                tables=(
                                    Table(
                                        id=table_id,
                                        name="orders",
                                        schema_id=make_schema_id(source, database, schema),
                                        description="orders table",
                                        ownership=Ownership(owner_name="governance_owner"),
                                        columns=(
                                            Column(
                                                id=column_id,
                                                name="order_id",
                                                data_type="bigint",
                                                ordinal_position=1,
                                                nullable=False,
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _write_workspace(tmp_path: Path) -> Path:
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = tmp_path / "governance.yaml"
    config.write_text(
        "\n".join(
            [
                'schema_version: "1"',
                "sources:",
                "  - id: primary",
                "    provider: postgresql",
                "    config:",
                "      source_name: governance-demo",
                "      connection:",
                "        database_url_env: DATABASE_URL",
                "targets:",
                "  - id: collibra",
                "    provider: collibra",
                "    config:",
                "      mode_env: COLLIBRA_MODE",
                "      execution_mode_env: COLLIBRA_EXECUTION_MODE",
                "      mapping:",
                "        path: mapping.json",
                "      auth:",
                "        base_url_env: COLLIBRA_BASE_URL",
                "        username_env: COLLIBRA_USERNAME",
                "        password_env: COLLIBRA_PASSWORD",
                "policies:",
                "  files: []",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "DATABASE_URL": "postgresql://postgres:super-secret-password@localhost:5432/governance_demo",
        "COLLIBRA_MODE": "live",
        "COLLIBRA_EXECUTION_MODE": "import_v2",
        "COLLIBRA_BASE_URL": "https://example.invalid",
        "COLLIBRA_USERNAME": "collibra-user",
        "COLLIBRA_PASSWORD": "collibra-secret-password",
        "COLLIBRA_BEARER_TOKEN": "",
        "COLLIBRA_CLIENT_ID": "",
        "COLLIBRA_CLIENT_SECRET": "",
        "COLLIBRA_TOKEN_URL": "",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _patch_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _tiny_model()

    class FakeScanner:
        def __init__(self, settings) -> None:
            self.settings = settings

        def scan(self) -> GovernanceModel:
            return model

    monkeypatch.setattr("governance.cli.PostgresMetadataScanner", FakeScanner)


def _empty_page() -> dict[str, object]:
    return {"results": [], "total": 0}


def _patch_live_import_http(
    monkeypatch: pytest.MonkeyPatch,
    requests: list[httpx.Request],
    *,
    job_id: str = "job-123",
    post_status: int = 200,
    post_body: dict[str, object] | None = None,
    collide_named_assets: bool = False,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and "/jobs/" in path:
            remote_job = path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={"id": remote_job, "state": "COMPLETED", "result": "SUCCESS"},
            )
        if request.method == "GET" and path.endswith("/assets"):
            if collide_named_assets and request.url.params.get("name"):
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": "unmanaged-1",
                                "name": request.url.params.get("name"),
                                "domain": {"id": "mock-domain"},
                            }
                        ],
                        "total": 1,
                    },
                )
            return httpx.Response(200, json=_empty_page())
        if request.method == "GET" and path.endswith(("/attributes", "/relations")):
            return httpx.Response(200, json=_empty_page())
        if request.method == "POST" and path.endswith("/import/json-job"):
            if post_status != 200:
                return httpx.Response(post_status, json={"error": "import failed"})
            body = {"id": job_id} if post_body is None else post_body
            return httpx.Response(200, json=body)
        return httpx.Response(500, json={"error": "unexpected"})

    def factory(settings, mapping_config, *, transport=None):
        del transport
        return LiveCollibraAdapter.from_settings(
            settings,
            mapping_config,
            transport=httpx.MockTransport(handler),
            sleeper=lambda _s: None,
        )

    monkeypatch.setattr("governance.cli.build_collibra_adapter", factory)


def _post_paths(requests: list[httpx.Request]) -> list[str]:
    return [urlparse(str(request.url)).path for request in requests if request.method == "POST"]


def _generate_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    requests: list[httpx.Request],
    capsys: pytest.CaptureFixture[str],
) -> tuple[Path, Path]:
    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_live_import_http(monkeypatch, requests)
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


def test_apply_import_v2_dry_run_zero_post(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    config, plan_path = _generate_plan(monkeypatch, tmp_path, requests, capsys)
    _patch_live_import_http(monkeypatch, requests)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert _post_paths(requests) == []
    assert payload["result_schema"] == "governance-import-submission-result"
    assert payload["execution_mode"] == "import_v2"
    assert payload["dry_run"] is True
    assert payload["submitted"] is False
    assert payload["job_id"] is None
    assert payload["applied_count"] == 0
    assert payload["job_terminal_status"] == "not_observed"
    assert "success" not in payload
    assert isinstance(payload["document"], list)
    assert payload["document"]


def test_apply_import_v2_submit_preserves_job_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    config, plan_path = _generate_plan(monkeypatch, tmp_path, requests, capsys)
    _patch_live_import_http(monkeypatch, requests, job_id="job-123")
    assert (
        main(
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
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert _post_paths(requests) == ["/rest/2.0/import/json-job"]
    assert any(
        request.method == "GET" and "/jobs/job-123" in urlparse(str(request.url)).path
        for request in requests
    )
    assert payload["result_schema"] == "governance-import-job-result"
    assert payload["job_id"] == "job-123"
    assert payload["success"] is True
    assert payload["dry_run"] is False
    assert payload["applied_count"] > 0
    assert payload["terminal"] is True


def test_sync_import_v2_dry_run_and_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_live_import_http(monkeypatch, requests)
    config = _write_workspace(tmp_path)
    mapping = tmp_path / "mapping.json"
    assert (
        main(
            [
                "sync",
                "--config",
                str(config),
                "--mode",
                "live",
                "--mapping-config",
                str(mapping),
                "--json",
            ]
        )
        == 0
    )
    dry = json.loads(capsys.readouterr().out)
    assert _post_paths(requests) == []
    assert dry["result_schema"] == "governance-import-submission-result"
    assert dry["execution_mode"] == "import_v2"
    assert dry["submitted"] is False
    assert dry["job_id"] is None
    assert dry["applied_count"] == 0
    assert dry["job_terminal_status"] == "not_observed"
    assert "success" not in dry
    assert dry["document"]

    requests.clear()
    _patch_live_import_http(monkeypatch, requests, job_id="job-123")
    assert (
        main(
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
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert _post_paths(requests) == ["/rest/2.0/import/json-job"]
    assert applied["result_schema"] == "governance-import-job-result"
    assert applied["mode"] == "live"
    assert applied["success"] is True
    assert applied["dry_run"] is False
    assert applied["applied_count"] > 0
    assert applied["job_id"] == "job-123"


def test_import_apply_human_does_not_claim_job_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    config, plan_path = _generate_plan(monkeypatch, tmp_path, requests, capsys)
    _patch_live_import_http(monkeypatch, requests, job_id="job-123")
    assert (
        main(
            [
                "apply",
                str(plan_path),
                "--config",
                str(config),
                "--apply",
                "--confirm-live",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "stale=false" in out
    assert "dry_run=false" in out
    assert "success=true" in out
    assert "job_id=job-123" in out
    assert "terminal=true" in out


def _assert_structured_import_failure(payload: object, captured: str) -> None:
    assert isinstance(payload, dict)
    assert payload["diagnostic_schema"] == "governance-operation-diagnostics"
    assert payload["ok"] is False
    assert payload.get("success") is not True
    assert payload.get("completed") is not True
    assert payload.get("applied_count", 0) == 0
    assert "job_terminal_status" not in payload or payload["job_terminal_status"] == "not_observed"
    lowered = captured.lower()
    assert "collibra-secret-password" not in lowered
    assert "super-secret-password" not in lowered
    assert "authorization" not in lowered
    assert "bearer " not in lowered


def test_apply_import_v2_post_500_returns_lifecycle_unknown_submission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    config, plan_path = _generate_plan(monkeypatch, tmp_path, requests, capsys)
    _patch_live_import_http(monkeypatch, requests, post_status=500)
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
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["result_schema"] == "governance-import-job-result"
    assert payload["submission_state"] == "unknown"
    assert payload["submitted"] is None
    assert payload["success"] is False
    assert payload["job_id"] is None
    lowered = (captured.out + captured.err).lower()
    assert "collibra-secret-password" not in lowered
    assert "super-secret-password" not in lowered
    assert any(
        request.method == "POST" and urlparse(str(request.url)).path.endswith("/import/json-job")
        for request in requests
    )


def test_sync_import_v2_post_500_returns_lifecycle_unknown_submission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_live_import_http(monkeypatch, requests, post_status=500)
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
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["result_schema"] == "governance-import-job-result"
    assert payload["submission_state"] == "unknown"
    assert payload["submitted"] is None
    assert payload["success"] is False


def test_apply_import_v2_collision_is_structured_json_zero_post(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    config, plan_path = _generate_plan(monkeypatch, tmp_path, requests, capsys)
    _patch_live_import_http(monkeypatch, requests, collide_named_assets=True)
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
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    _assert_structured_import_failure(payload, captured.out + captured.err)
    assert _post_paths(requests) == []
    assert "collides" in json.dumps(payload).lower()


def test_apply_import_v2_missing_job_id_is_structured_json_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    config, plan_path = _generate_plan(monkeypatch, tmp_path, requests, capsys)
    _patch_live_import_http(monkeypatch, requests, post_body={})
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
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    if payload.get("diagnostic_schema") == "governance-operation-diagnostics":
        _assert_structured_import_failure(payload, captured.out + captured.err)
        dumped = json.dumps(payload).lower()
        assert "missing id" in dumped
        return
    assert payload.get("success") is not True
    assert payload.get("completed") is not True
    assert payload.get("applied_count", 0) == 0
    assert "completed" not in json.dumps(payload).lower() or payload.get("completed") is not True
    message = json.dumps(payload).lower()
    assert "uncertain" in message or "missing id" in message
    lowered = (captured.out + captured.err).lower()
    assert "collibra-secret-password" not in lowered


def test_apply_import_v2_post_500_human_reports_lifecycle_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []
    config, plan_path = _generate_plan(monkeypatch, tmp_path, requests, capsys)
    _patch_live_import_http(monkeypatch, requests, post_status=500)
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
    captured = capsys.readouterr()
    assert code == 1
    assert "submission_state=unknown" in captured.out
    assert "success=false" in captured.out
    combined = (captured.out + captured.err).lower()
    assert "collibra-secret-password" not in combined
