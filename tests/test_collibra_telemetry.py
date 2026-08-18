"""Secret-safe Collibra operational telemetry tests."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
import pytest

from governance.config import Settings
from governance.identity import plan_identity
from governance.identity.hashing import ContentIdentity
from governance.integrations.collibra import (
    CollibraAdapterError,
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraAuthError,
    CollibraDesiredState,
    CollibraRemoteState,
    LiveCollibraAdapter,
    MockCollibraAdapter,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
    execute_collibra_plan,
    mock_mapping_config,
)
from governance.integrations.collibra.import_api import ImportSubmission
from governance.integrations.collibra.preflight import (
    CODE_REMOTE_HTTP,
    STATUS_INCOMPATIBLE,
    PreflightCheck,
    PreflightReport,
)
from governance.integrations.collibra.telemetry import (
    ALLOWED_KEYS,
    JsonlSink,
    RecordingSink,
    bound_sink,
    build_event,
    emit,
    endpoint_template,
    execution_scope,
    sink_from_environ,
)
from governance.plans import SavedGovernancePlan
from governance.plans.apply_result import build_apply_result
from support.collibra_contract_server import (
    CONTRACT_CLIENT_SECRET,
    CONTRACT_TOKEN,
    CollibraContractServer,
)


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
    assert "password" not in str(event)
    assert "postgresql://" not in str(event)


def test_endpoint_template_strips_dynamic_ids_and_queries() -> None:
    assert endpoint_template("/rest/2.0/jobs/job-import-0?token=secret") == "/rest/2.0/jobs/{id}"
    assert endpoint_template("/rest/2.0/assets/custom-asset-id") == "/rest/2.0/assets/{id}"
    assert endpoint_template("/rest/2.0/domains/custom-domain-ref") == "/rest/2.0/domains/{id}"
    assert (
        endpoint_template("/rest/2.0/import/results/job-42/errors")
        == "/rest/2.0/import/results/{id}/errors"
    )
    assert endpoint_template("/rest/2.0/assets") == "/rest/2.0/assets"
    templated = endpoint_template("/rest/2.0/jobs/job-import-0?token=secret")
    assert "job-import-0" not in templated
    assert "token" not in templated
    assert "secret" not in templated
    assert build_event({"submission_state": "weird"})["submission_state"] == "unknown"
    assert build_event({"submission_state": "submitted"})["submission_state"] == "submitted"


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
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes
    assert outcomes[0].get("duration_ms", 0) >= 0


def _create_plan() -> SyncPlan:
    config = mock_mapping_config()
    asset = CollibraAssetSpec(
        local_id="tbl:orders",
        name="orders",
        display_name="orders",
        asset_type_ref=config.asset_type_refs["table"],
        domain_ref=config.domain_ref,
        attributes=(CollibraAttributeSpec(config.attribute_type_refs["local_id"], "tbl:orders"),),
    )
    return SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id=asset.local_id,
                reason="create",
                desired_asset=asset,
            ),
        )
    )


def test_nested_execution_scope_reuses_one_correlation() -> None:
    sink = RecordingSink()
    with execution_scope(sink=sink, execution_mode="core_rest"):
        execute_collibra_plan(
            MockCollibraAdapter(mock_mapping_config()),
            SyncPlan(actions=()),
            mock_mapping_config(),
            apply=False,
            execution_mode="core_rest",
        )
    ids = {event["correlation_id"] for event in sink.events if "correlation_id" in event}
    assert len(ids) == 1
    operations = [event.get("operation") for event in sink.events]
    assert operations.count("execution_start") == 1
    assert operations.count("execution_outcome") == 1


def test_dry_run_writes_performed_is_zero() -> None:
    sink = RecordingSink()
    with bound_sink(sink):
        execute_collibra_plan(
            MockCollibraAdapter(mock_mapping_config()),
            _create_plan(),
            mock_mapping_config(),
            apply=False,
            execution_mode="core_rest",
        )
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["writes_performed"] == 0
    assert outcomes[0]["outcome"] == "success"


def test_confirmed_success_includes_writes_performed() -> None:
    sink = RecordingSink()
    with bound_sink(sink):
        result = execute_collibra_plan(
            MockCollibraAdapter(mock_mapping_config()),
            _create_plan(),
            mock_mapping_config(),
            apply=True,
            execution_mode="core_rest",
        )
    assert result.success is True
    assert result.applied_count == 1
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["writes_performed"] == 1


def test_uncertain_import_submission_omits_writes_performed() -> None:
    sink = RecordingSink()

    class UncertainAdapter:
        mode = "live"

        def lookup_assets_by_natural_identifier(
            self, *, name: str, domain_ref: str
        ) -> list[object]:
            del name, domain_ref
            return []

        def submit_json_import(self, document: object) -> None:
            del document
            raise CollibraAdapterError("timeout", operation="submit_json_import")

        def poll_job(self, job_id: str) -> dict[str, str]:
            del job_id
            raise AssertionError("poll must not run")

    with bound_sink(sink):
        result = execute_collibra_plan(
            UncertainAdapter(),
            _create_plan(),
            mock_mapping_config(),
            apply=True,
            execution_mode="import_v2",
        )
    assert result.success is False
    batches = [event for event in sink.events if event.get("operation") == "import_batch"]
    assert batches
    assert batches[0]["submission_state"] == "unknown"
    assert "job_id" not in batches[0]
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert "writes_performed" not in outcomes[0]
    assert outcomes[0].get("writes_performed") != 0


def test_empty_import_job_id_emits_unknown_submission() -> None:
    sink = RecordingSink()

    class EmptyJobAdapter:
        mode = "live"

        def lookup_assets_by_natural_identifier(
            self, *, name: str, domain_ref: str
        ) -> list[object]:
            del name, domain_ref
            return []

        def submit_json_import(self, document: object) -> ImportSubmission:
            del document
            return ImportSubmission(job_id="")

        def poll_job(self, job_id: str) -> dict[str, str]:
            del job_id
            raise AssertionError("poll must not run")

    with bound_sink(sink):
        result = execute_collibra_plan(
            EmptyJobAdapter(),
            _create_plan(),
            mock_mapping_config(),
            apply=True,
            execution_mode="import_v2",
        )
    assert result.success is False
    batches = [event for event in sink.events if event.get("operation") == "import_batch"]
    assert batches[0]["submission_state"] == "unknown"
    assert "job_id" not in batches[0]


def test_sync_batch_and_finalize_submission_states() -> None:
    sink = RecordingSink()
    sync_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    class UncertainSyncAdapter:
        mode = "live"

        def lookup_assets_by_natural_identifier(
            self, *, name: str, domain_ref: str
        ) -> list[object]:
            del name, domain_ref
            return []

        def submit_sync_batch(self, synchronization_id: str, document: object) -> None:
            del synchronization_id, document
            raise CollibraAdapterError("timeout", operation="submit_sync_batch")

        def poll_job(self, job_id: str) -> dict[str, str]:
            del job_id
            raise AssertionError("poll must not run")

        def submit_sync_finalize(
            self, synchronization_id: str, *, strategy: str = "IGNORE"
        ) -> None:
            del synchronization_id, strategy
            raise AssertionError("finalize must not run")

    with bound_sink(sink):
        result = execute_collibra_plan(
            UncertainSyncAdapter(),
            _create_plan(),
            mock_mapping_config(),
            apply=True,
            execution_mode="sync_v2",
            synchronization_id=sync_id,
        )
    assert result.success is False
    batches = [event for event in sink.events if event.get("operation") == "sync_batch"]
    assert batches[0]["submission_state"] == "unknown"
    assert "job_id" not in batches[0]
    assert not any(event.get("operation") == "sync_finalize" for event in sink.events)
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert "writes_performed" not in outcomes[0]


def test_sync_finalize_emits_submitted_then_terminal_view() -> None:
    sink = RecordingSink()
    sync_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    class SuccessSyncAdapter:
        mode = "live"

        def lookup_assets_by_natural_identifier(
            self, *, name: str, domain_ref: str
        ) -> list[object]:
            del name, domain_ref
            return []

        def submit_sync_batch(self, synchronization_id: str, document: object) -> ImportSubmission:
            del synchronization_id, document
            return ImportSubmission(job_id="job-batch-0")

        def poll_job(self, job_id: str):
            from governance.integrations.collibra.jobs import classify_job

            return classify_job(
                {"id": job_id, "state": "COMPLETED", "result": "SUCCESS"},
                job_id=job_id,
            )

        def submit_sync_finalize(
            self, synchronization_id: str, *, strategy: str = "IGNORE"
        ) -> ImportSubmission:
            del synchronization_id, strategy
            return ImportSubmission(job_id="job-finalize")

    with bound_sink(sink):
        result = execute_collibra_plan(
            SuccessSyncAdapter(),
            _create_plan(),
            mock_mapping_config(),
            apply=True,
            execution_mode="sync_v2",
            synchronization_id=sync_id,
        )
    assert result.success is True
    batches = [event for event in sink.events if event.get("operation") == "sync_batch"]
    assert batches[0]["submission_state"] == "submitted"
    assert batches[0]["job_id"] == "job-batch-0"
    finals = [event for event in sink.events if event.get("operation") == "sync_finalize"]
    assert finals[0]["submission_state"] == "submitted"
    assert finals[0]["job_id"] == "job-finalize"
    assert any(event.get("remote_state") == "COMPLETED" for event in finals)
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["writes_performed"] == 1


def test_oauth_transport_failure_includes_duration() -> None:
    sink = RecordingSink()

    class TimeoutTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            del request
            raise httpx.ConnectTimeout("timed out")

    adapter = LiveCollibraAdapter.from_settings(
        _settings(
            collibra_bearer_token="",
            collibra_client_id="cid",
            collibra_client_secret="csecret",
        ),
        mock_mapping_config(),
        transport=TimeoutTransport(),
    )
    with (
        execution_scope(sink=sink, execution_mode="core_rest"),
        pytest.raises(CollibraAuthError),
    ):
        adapter.read_json("/rest/2.0/application/info")
    tokens = [event for event in sink.events if event.get("operation") == "oauth_token"]
    assert tokens
    assert tokens[0].get("duration_ms", -1) >= 0
    assert tokens[0].get("outcome") == "error"
    assert "status" not in tokens[0]
    assert "csecret" not in str(sink.events)


def test_sink_failure_does_not_change_result_or_identity() -> None:
    class BoomSink:
        def emit(self, event: dict[str, object]) -> None:
            del event
            raise OSError("boom")

    plan = _create_plan()
    adapter = MockCollibraAdapter(mock_mapping_config())
    before_plan = plan.to_dict()
    before_digest = plan_identity(before_plan).digest
    with execution_scope(sink=BoomSink(), execution_mode="core_rest"):
        result = execute_collibra_plan(
            adapter,
            plan,
            mock_mapping_config(),
            apply=False,
            execution_mode="core_rest",
        )
    control = execute_collibra_plan(
        MockCollibraAdapter(mock_mapping_config()),
        plan,
        mock_mapping_config(),
        apply=False,
        execution_mode="core_rest",
    )
    assert result.success is True
    assert result.dry_run is True
    assert result.applied_count == control.applied_count
    assert plan.to_dict() == before_plan
    assert plan_identity(plan.to_dict()).digest == before_digest
    payload = build_apply_result(
        sync_plan=plan,
        result=result,
        plan_content_identity=plan_identity(plan.to_dict()),
    )
    blob = str(payload)
    assert "correlation_id" not in payload
    assert "telemetry" not in payload
    assert "duration_ms" not in blob


def test_jsonl_sink_without_path_writes_stderr_not_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    JsonlSink().emit({"operation": "execution_start"})
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "execution_start" in captured.err


def test_end_to_end_import_uses_one_correlation() -> None:
    sink = RecordingSink()
    clock = FakeClock()
    with CollibraContractServer() as server:
        adapter = LiveCollibraAdapter.from_settings(
            _settings(
                collibra_base_url=server.base_url,
                collibra_bearer_token="",
                collibra_client_id="contract-client",
                collibra_client_secret=CONTRACT_CLIENT_SECRET,
            ),
            mock_mapping_config(),
            monotonic_clock=clock,
            sleeper=clock.sleep,
        )
        with execution_scope(sink=sink, execution_mode="import_v2"):
            adapter.read_remote_state(CollibraDesiredState(assets=()))
            result = execute_collibra_plan(
                adapter,
                _create_plan(),
                mock_mapping_config(),
                apply=True,
                execution_mode="import_v2",
            )
    assert result.success is True
    operations = {event.get("operation") for event in sink.events}
    assert "oauth_token" in operations
    assert "http_attempt" in operations
    assert "import_batch" in operations
    assert "job_poll" in operations
    assert "execution_outcome" in operations
    assert [event.get("operation") for event in sink.events].count("execution_start") == 1
    ids = {event["correlation_id"] for event in sink.events if "correlation_id" in event}
    assert len(ids) == 1
    for event in sink.events:
        if "correlation_id" in event:
            assert event["correlation_id"] == next(iter(ids))
        if "endpoint_path" in event:
            assert "job-import-0" not in str(event["endpoint_path"])
            assert "?" not in str(event["endpoint_path"])
            assert "token=" not in str(event["endpoint_path"])
    blob = str(sink.events)
    assert CONTRACT_TOKEN not in blob
    assert CONTRACT_CLIENT_SECRET not in blob
    assert "Authorization" not in blob
    assert "Bearer " not in blob
    assert "access_token" not in blob
    assert "client_secret" not in blob
    assert "password" not in blob
    batches = [event for event in sink.events if event.get("operation") == "import_batch"]
    assert batches[0]["submission_state"] == "submitted"
    assert batches[0]["job_id"] == "job-import-0"
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert len(outcomes) == 1
    assert "writes_performed" in outcomes[0]
    assert outcomes[0]["duration_ms"] >= 0


def _identity(digest: str) -> ContentIdentity:
    return ContentIdentity(
        algorithm="sha256",
        hashing_contract_version="1",
        digest=digest,
    )


def _operation_counts(sink: RecordingSink) -> tuple[int, int]:
    operations = [event.get("operation") for event in sink.events]
    return operations.count("execution_start"), operations.count("execution_outcome")


class _EmptyRemoteAdapter:
    def read_remote_state(self, desired: object) -> CollibraRemoteState:
        del desired
        return CollibraRemoteState()


def test_root_scope_default_success_outcome() -> None:
    sink = RecordingSink()
    with execution_scope(
        sink=sink,
        execution_mode="core_rest",
        default_outcome="success",
        default_writes_performed=0,
    ):
        emit(operation="http_attempt", execution_mode="core_rest", status=200)
    starts, outcomes_count = _operation_counts(sink)
    assert starts == 1
    assert outcomes_count == 1
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["outcome"] == "success"
    assert outcomes[0]["writes_performed"] == 0
    assert outcomes[0]["duration_ms"] >= 0


def test_root_scope_exception_emits_error_outcome() -> None:
    sink = RecordingSink()
    with (
        pytest.raises(RuntimeError, match="secret-boom"),
        execution_scope(sink=sink, execution_mode="core_rest"),
    ):
        raise RuntimeError("secret-boom")
    starts, outcomes_count = _operation_counts(sink)
    assert starts == 1
    assert outcomes_count == 1
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["outcome"] == "error"
    blob = str(sink.events)
    assert "secret-boom" not in blob
    assert "RuntimeError" not in blob


def test_nested_executor_emits_exactly_one_outcome() -> None:
    sink = RecordingSink()
    with execution_scope(
        sink=sink,
        execution_mode="core_rest",
        default_outcome="success",
        default_writes_performed=0,
    ):
        MockCollibraAdapter(mock_mapping_config()).read_remote_state(
            CollibraDesiredState(assets=())
        )
        execute_collibra_plan(
            MockCollibraAdapter(mock_mapping_config()),
            SyncPlan(actions=()),
            mock_mapping_config(),
            apply=False,
            execution_mode="core_rest",
        )
    starts, outcomes_count = _operation_counts(sink)
    assert starts == 1
    assert outcomes_count == 1
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["outcome"] == "success"


def test_root_duration_includes_pre_execution_work(monkeypatch: pytest.MonkeyPatch) -> None:
    sink = RecordingSink()
    clock = FakeClock()
    monkeypatch.setattr(
        "governance.integrations.collibra.telemetry.time.monotonic",
        clock,
    )
    with execution_scope(sink=sink, execution_mode="core_rest"):
        clock.now += 2.0
        execute_collibra_plan(
            MockCollibraAdapter(mock_mapping_config()),
            SyncPlan(actions=()),
            mock_mapping_config(),
            apply=False,
            execution_mode="core_rest",
        )
    starts, outcomes_count = _operation_counts(sink)
    assert starts == 1
    assert outcomes_count == 1
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["duration_ms"] >= 2000


def test_cli_diff_successful_read_emits_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from governance.cli import _cmd_diff

    sink = RecordingSink()
    monkeypatch.setattr("governance.cli._scan_model", lambda settings: object())
    monkeypatch.setattr(
        "governance.cli.map_to_desired_state",
        lambda model, mapping: CollibraDesiredState(assets=()),
    )
    monkeypatch.setattr(
        "governance.cli.build_collibra_adapter",
        lambda settings, mapping: _EmptyRemoteAdapter(),
    )
    with bound_sink(sink):
        code = _cmd_diff(
            _settings(collibra_mode="mock"),
            mode="mock",
            mapping_config_path=None,
            json_output=True,
        )
    capsys.readouterr()
    assert code == 0
    starts, outcomes_count = _operation_counts(sink)
    assert starts == 1
    assert outcomes_count == 1
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["outcome"] == "success"
    assert outcomes[0]["writes_performed"] == 0


def test_cli_sync_preread_error_emits_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from governance.cli import _cmd_sync

    class BoomAdapter:
        def read_remote_state(self, desired: object) -> CollibraRemoteState:
            del desired
            raise CollibraAdapterError("read failed", operation="get")

    sink = RecordingSink()
    monkeypatch.setattr("governance.cli._scan_model", lambda settings: object())
    monkeypatch.setattr(
        "governance.cli.map_to_desired_state",
        lambda model, mapping: CollibraDesiredState(assets=()),
    )
    monkeypatch.setattr(
        "governance.cli.build_collibra_adapter",
        lambda settings, mapping: BoomAdapter(),
    )
    with bound_sink(sink), pytest.raises(CollibraAdapterError):
        _cmd_sync(
            _settings(collibra_mode="mock"),
            mode="mock",
            mapping_config_path=None,
            apply=False,
            json_output=True,
        )
    starts, outcomes_count = _operation_counts(sink)
    assert starts == 1
    assert outcomes_count == 1
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["outcome"] == "error"
    blob = str(sink.events)
    assert "read failed" not in blob


def test_cli_apply_stale_emits_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from governance.cli import _cmd_apply

    sink = RecordingSink()
    dummy = _identity("aa" * 32)
    saved = SavedGovernancePlan(
        sync_plan=SyncPlan(actions=()),
        config_identity=dummy,
        snapshot_identity=dummy,
        policy_identity=dummy,
        mapping_identity=dummy,
        target_context={},
        target_context_identity=_identity("bb" * 32),
        remote_state_identity=dummy,
    )
    canonical = SimpleNamespace(
        targets=(object(),),
        identity_projection=lambda: {"schema_version": "1"},
    )
    settings = _settings(collibra_mode="mock")

    class _Snap:
        def content_identity(self) -> ContentIdentity:
            return dummy

    class _GS:
        @staticmethod
        def from_model(model: object) -> _Snap:
            del model
            return _Snap()

    class _Policies:
        def to_identity_dict(self) -> dict[str, object]:
            return {}

    monkeypatch.setattr("governance.cli.load_saved_plan", lambda path: saved)
    monkeypatch.setattr(
        "governance.cli._load_canonical_and_settings",
        lambda **kwargs: (canonical, settings),
    )
    monkeypatch.setattr("governance.cli.load_normalized_policies", lambda canonical: _Policies())
    monkeypatch.setattr("governance.cli.validate_collibra_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr("governance.cli._scan_model", lambda settings: object())
    monkeypatch.setattr("governance.cli.GovernanceSnapshot", _GS)
    args = Namespace(
        format="json",
        apply=False,
        confirm_live=False,
        plan_file="plan.gplan",
        config="governance.yaml",
        profile=None,
    )
    with bound_sink(sink):
        code = _cmd_apply(args)
    capsys.readouterr()
    assert code == 5
    starts, outcomes_count = _operation_counts(sink)
    assert starts == 1
    assert outcomes_count == 1
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["outcome"] == "failure"
    assert outcomes[0]["writes_performed"] == 0


def test_cli_preflight_incompatible_emits_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from governance.cli import _cmd_preflight

    sink = RecordingSink()
    canonical = SimpleNamespace(targets=(object(),))
    settings = _settings(collibra_mode="mock")
    report = PreflightReport(
        overall=STATUS_INCOMPATIBLE,
        mode="mock",
        transport=None,
        writes_performed=0,
        checks=(
            PreflightCheck(
                id="transport",
                status=STATUS_INCOMPATIBLE,
                code=CODE_REMOTE_HTTP,
                message="remote http rejected",
                blocking=True,
            ),
        ),
    )
    monkeypatch.setattr(
        "governance.cli._load_canonical_and_settings",
        lambda **kwargs: (canonical, settings),
    )
    monkeypatch.setattr("governance.cli.run_preflight", lambda settings, mapping: report)
    args = Namespace(format="json", config="governance.yaml", profile=None)
    with bound_sink(sink):
        code = _cmd_preflight(args)
    captured = capsys.readouterr()
    assert code == 1
    assert '"overall": "INCOMPATIBLE"' in captured.out or '"overall":"INCOMPATIBLE"' in captured.out
    starts, outcomes_count = _operation_counts(sink)
    assert starts == 1
    assert outcomes_count == 1
    outcomes = [event for event in sink.events if event.get("operation") == "execution_outcome"]
    assert outcomes[0]["outcome"] == "failure"
    assert outcomes[0]["writes_performed"] == 0
    assert report.overall == STATUS_INCOMPATIBLE
    assert report.writes_performed == 0
