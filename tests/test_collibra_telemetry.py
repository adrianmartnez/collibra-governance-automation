"""Secret-safe Collibra operational telemetry tests."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from governance.config import Settings
from governance.identity import plan_identity
from governance.integrations.collibra import (
    CollibraAuthError,
    CollibraDesiredState,
    LiveCollibraAdapter,
    MockCollibraAdapter,
    SyncPlan,
    execute_collibra_plan,
    mock_mapping_config,
)
from governance.integrations.collibra.telemetry import (
    ALLOWED_KEYS,
    RecordingSink,
    bound_sink,
    build_event,
    execution_scope,
    sink_from_environ,
)
from governance.plans.apply_result import build_apply_result


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "governance_demo",
        "postgres_user": "postgres",
        "postgres_password": "postgres",
        "postgres_source_name": "governance-demo",
        "inventory_output_path": "artifacts/metadata-inventory.json",
        "collibra_mode": "live",
        "collibra_base_url": "https://collibra.example.invalid",
        "collibra_bearer_token": "secret-token-value-xyz",
        "collibra_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return Settings(**values)


def test_build_event_drops_secrets_and_unknown_keys() -> None:
    event = build_event(
        {
            "authorization": "Bearer secret-token-value-xyz",
            "access_token": "secret-token-value-xyz",
            "client_secret": "super-secret",
            "body": '{"password":"nope"}',
            "operation": "oauth_token",
            "endpoint_path": "/rest/oauth/v2/token?client_secret=super-secret",
            "status": 401,
        }
    )
    assert "authorization" not in event
    assert "access_token" not in event
    assert "client_secret" not in event
    assert "body" not in event
    assert event["operation"] == "oauth_token"
    assert "client_secret" not in event["endpoint_path"]
    assert "super-secret" not in str(event)
    assert "secret-token-value-xyz" not in str(event)
    assert set(event) <= ALLOWED_KEYS


def test_default_sink_is_null() -> None:
    assert type(sink_from_environ({})).__name__ == "NullSink"


def test_jsonl_sink_writes_file_not_plan_hash(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    plan = SyncPlan(actions=())
    before = plan_identity(plan.to_dict()).digest
    sink = sink_from_environ({"COLLIBRA_TELEMETRY": "jsonl", "COLLIBRA_TELEMETRY_PATH": str(path)})
    with execution_scope(execution_mode="core_rest", sink=sink):
        execute_collibra_plan(
            MockCollibraAdapter(mock_mapping_config()),
            plan,
            mock_mapping_config(),
            apply=False,
            execution_mode="core_rest",
        )
    after = plan_identity(plan.to_dict()).digest
    assert after == before
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    blob = path.read_text(encoding="utf-8")
    assert "secret-token-value-xyz" not in blob
    assert "correlation_id" in blob
    result = execute_collibra_plan(
        MockCollibraAdapter(mock_mapping_config()),
        plan,
        mock_mapping_config(),
        apply=False,
        execution_mode="core_rest",
    )
    payload = build_apply_result(
        sync_plan=plan,
        result=result,
        plan_content_identity=plan_identity(plan.to_dict()),
    )
    assert "correlation_id" not in payload
    assert "telemetry" not in payload


def test_http_retries_and_oauth_errors_are_redacted() -> None:
    sink = RecordingSink()
    statuses = [429, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method.upper() == "GET":
            code = statuses.pop(0) if statuses else 200
            headers = {"Retry-After": "0"} if code == 429 else {}
            return httpx.Response(code, json={"results": []}, headers=headers)
        return httpx.Response(404, json={"error": "not-found"})

    clock = FakeClock()
    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
        monotonic_clock=clock,
        sleeper=clock.sleep,
        page_size=10,
    )
    with execution_scope(sink=sink, execution_mode="core_rest"):
        adapter.read_remote_state(CollibraDesiredState(assets=()))
    attempts = [event for event in sink.events if event.get("operation") == "http_attempt"]
    assert any(event.get("status") == 429 and event.get("attempt") == 1 for event in attempts)
    assert any(event.get("status") == 200 for event in attempts)
    blob = str(sink.events)
    assert "secret-token-value-xyz" not in blob
    assert "Authorization" not in blob
    ids = {event.get("correlation_id") for event in sink.events if "correlation_id" in event}
    assert len(ids) == 1


def test_oauth_failure_emits_status_without_secrets() -> None:
    sink = RecordingSink()

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if request.method.upper() == "POST" and path.endswith("/oauth/v2/token"):
            return httpx.Response(401, json={"error": "unauthorized", "access_token": "leak-me"})
        return httpx.Response(404)

    adapter = LiveCollibraAdapter.from_settings(
        _settings(
            collibra_bearer_token="",
            collibra_client_id="cid",
            collibra_client_secret="csecret",
        ),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
    )
    with (
        execution_scope(sink=sink, execution_mode="core_rest"),
        pytest.raises(CollibraAuthError),
    ):
        adapter.read_json("/rest/2.0/application/info")
    blob = str(sink.events)
    assert "csecret" not in blob
    assert "leak-me" not in blob
    assert any(
        event.get("operation") == "oauth_token" and event.get("status") == 401
        for event in sink.events
    )


def test_bound_sink_receives_import_outcome() -> None:
    sink = RecordingSink()
    with bound_sink(sink):
        execute_collibra_plan(
            MockCollibraAdapter(mock_mapping_config()),
            SyncPlan(actions=()),
            mock_mapping_config(),
            apply=False,
            execution_mode="core_rest",
        )
    operations = [event.get("operation") for event in sink.events]
    assert "execution_start" in operations
    assert "execution_outcome" in operations
    assert any(event.get("writes_performed") == 0 for event in sink.events)
