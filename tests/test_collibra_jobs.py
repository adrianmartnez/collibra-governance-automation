"""Job lifecycle, IGNORE-only finalization, and sync identity tests."""

from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID

import httpx
import pytest

from governance.config import Settings
from governance.identity import target_context_identity
from governance.integrations.collibra import (
    CollibraAdapterError,
    CollibraAssetSpec,
    CollibraAttributeSpec,
    LiveCollibraAdapter,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
    execute_collibra_plan,
    mock_mapping_config,
)
from governance.integrations.collibra.import_api import IMPORT_MULTIPART_FIELDS
from governance.integrations.collibra.jobs import (
    classify_job,
    poll_until_terminal,
)
from governance.integrations.collibra.synchronization import (
    derive_synchronization_id,
    effective_synchronization_id,
    require_ignore_strategy,
)
from governance.plans.target_context import (
    build_target_context_projection,
    target_context_public,
)

UUID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
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
        "collibra_execution_mode": "sync_v2",
    }
    base.update(overrides)
    return Settings(**base)


def _create_plan() -> SyncPlan:
    config = mock_mapping_config()
    asset = CollibraAssetSpec(
        local_id="tbl:demo",
        name="demo",
        display_name="demo",
        asset_type_ref=config.asset_type_refs["table"],
        domain_ref=config.domain_ref,
        attributes=(CollibraAttributeSpec(config.attribute_type_refs["local_id"], "tbl:demo"),),
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


@pytest.mark.parametrize(
    ("payload", "normalized_state", "normalized_outcome"),
    [
        ({"state": "WAITING"}, "non_terminal", None),
        ({"state": "RUNNING", "result": "NOT_SET"}, "non_terminal", None),
        ({"state": "CANCELING"}, "non_terminal", None),
        ({"state": "COMPLETED", "result": "SUCCESS"}, "terminal_success", "success"),
        (
            {"state": "COMPLETED", "result": "COMPLETED_WITH_ERROR"},
            "terminal_failure",
            "completed_with_error",
        ),
        ({"state": "COMPLETED", "result": "FAILURE"}, "terminal_failure", "failure"),
        ({"state": "COMPLETED", "result": "ABORTED"}, "terminal_failure", "aborted"),
        ({"state": "COMPLETED", "result": "NOT_SET"}, "uncertain", "uncertain"),
        ({"state": "COMPLETED"}, "uncertain", "uncertain"),
        ({"state": "CANCELED", "result": "SUCCESS"}, "cancelled", "cancelled"),
        ({"state": "ERROR"}, "terminal_failure", "failure"),
        ({"state": "NEW_VENDOR_STATE"}, "unknown", "unknown"),
        ({"state": "COMPLETED", "result": "NEW_VENDOR_RESULT"}, "unknown", "unknown"),
        ("not-a-job", "uncertain", "uncertain"),
    ],
)
def test_job_matrix(payload: object, normalized_state: str, normalized_outcome: str | None) -> None:
    view = classify_job(payload)
    assert view.normalized_state == normalized_state
    assert view.normalized_outcome == normalized_outcome
    if normalized_state == "terminal_success":
        assert normalized_outcome == "success"


def test_canceling_then_canceled_is_not_success() -> None:
    states = iter(
        [
            {"id": "j1", "state": "CANCELING"},
            {"id": "j1", "state": "CANCELED", "result": "ABORTED"},
        ]
    )
    clock = FakeClock()
    view = poll_until_terminal(
        lambda _job_id: next(states),
        "j1",
        monotonic_clock=clock,
        sleeper=clock.sleep,
    )
    assert view.normalized_state == "cancelled"
    assert view.normalized_outcome == "cancelled"


def test_poll_timeout_on_persistent_canceling() -> None:
    clock = FakeClock()
    view = poll_until_terminal(
        lambda _job_id: {"id": "j1", "state": "CANCELING"},
        "j1",
        monotonic_clock=clock,
        sleeper=clock.sleep,
        timeout_seconds=2.0,
        interval_seconds=1.0,
    )
    assert view.normalized_state == "timeout"
    assert view.normalized_outcome == "timeout"


def test_completed_aborted_is_not_cancelled() -> None:
    view = classify_job({"state": "COMPLETED", "result": "ABORTED"})
    assert view.normalized_outcome == "aborted"
    assert view.normalized_state == "terminal_failure"


def test_forbidden_finalization_strategies_rejected_before_http() -> None:
    with pytest.raises(CollibraAdapterError, match="forbidden"):
        require_ignore_strategy("REMOVE_RESOURCES")
    with pytest.raises(CollibraAdapterError, match="forbidden"):
        require_ignore_strategy("CHANGE_STATUS")


def _sync_adapter(
    handler,
    *,
    clock: FakeClock | None = None,
) -> LiveCollibraAdapter:
    fake = clock or FakeClock()
    return LiveCollibraAdapter.from_settings(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
        monotonic_clock=fake,
        sleeper=fake.sleep,
    )


def test_sync_v2_success_polls_finalize_job() -> None:
    requests: list[httpx.Request] = []
    jobs = {
        "batch-1": {"id": "batch-1", "state": "COMPLETED", "result": "SUCCESS"},
        "fin-1": {"id": "fin-1", "state": "COMPLETED", "result": "SUCCESS"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        body = request.content.decode("utf-8", errors="replace")
        assert "REMOVE_RESOURCES" not in body
        assert "CHANGE_STATUS" not in body
        assert "secret-token-value-xyz" not in body
        if request.method == "POST" and path.endswith("/batch/json-job"):
            for key, value in IMPORT_MULTIPART_FIELDS.items():
                assert key in body
                assert value in body
            assert "/json-job" in path
            assert "/batch/" in path
            return httpx.Response(200, json={"id": "batch-1"})
        if request.method == "POST" and path.endswith("/finalize/job"):
            assert "finalizationStrategy" in body
            assert "IGNORE" in body
            return httpx.Response(200, json={"id": "fin-1"})
        if request.method == "GET" and "/jobs/" in path:
            job_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=jobs[job_id])
        if "/import/synchronize/" in path and path.endswith("/json-job") and "/batch/" not in path:
            raise AssertionError("combined synchronize json-job is forbidden")
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert result.success is True
    methods_paths = [(item.method, urlparse(str(item.url)).path) for item in requests]
    assert methods_paths[0][0] == "POST"
    assert methods_paths[0][1].endswith(f"/import/synchronize/{UUID_A}/batch/json-job")
    assert ("GET", "/rest/2.0/jobs/batch-1") in methods_paths
    assert any(path.endswith("/finalize/job") for _method, path in methods_paths)
    assert ("GET", "/rest/2.0/jobs/fin-1") in methods_paths


def test_batch_failure_never_sends_finalize() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "POST" and path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if request.method == "GET" and path.endswith("/jobs/batch-1"):
            return httpx.Response(
                200, json={"id": "batch-1", "state": "COMPLETED", "result": "FAILURE"}
            )
        if path.endswith("/finalize/job"):
            raise AssertionError("finalize must not be sent")
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert result.success is False
    assert "failure" in (result.error or "")
    assert not any(urlparse(str(item.url)).path.endswith("/finalize/job") for item in requests)


@pytest.mark.parametrize(
    ("finalize_payload", "needle"),
    [
        ({"id": "fin-1", "state": "ERROR"}, "failure"),
        ({"id": "fin-1", "state": "COMPLETED", "result": "FAILURE"}, "failure"),
        ({"id": "fin-1", "state": "NEW_STATE"}, "unknown"),
        ({"id": "fin-1", "state": "COMPLETED", "result": "WEIRD"}, "unknown"),
    ],
)
def test_finalize_job_non_success_is_not_overall_success(
    finalize_payload: dict[str, str], needle: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if path.endswith("/jobs/batch-1"):
            return httpx.Response(
                200, json={"id": "batch-1", "state": "COMPLETED", "result": "SUCCESS"}
            )
        if path.endswith("/finalize/job"):
            return httpx.Response(200, json={"id": "fin-1"})
        if path.endswith("/jobs/fin-1"):
            return httpx.Response(200, json=finalize_payload)
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert result.success is False
    assert needle in (result.error or "")


def test_malformed_finalize_submission_is_uncertain_without_retry() -> None:
    finalize_posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal finalize_posts
        path = urlparse(str(request.url)).path
        if path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if path.endswith("/jobs/batch-1"):
            return httpx.Response(
                200, json={"id": "batch-1", "state": "COMPLETED", "result": "SUCCESS"}
            )
        if path.endswith("/finalize/job"):
            finalize_posts += 1
            return httpx.Response(200, json={"name": "missing-id"})
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert result.success is False
    assert "uncertain" in (result.error or "")
    assert finalize_posts == 1


def test_finalize_timeout_is_not_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if path.endswith("/jobs/batch-1"):
            return httpx.Response(
                200, json={"id": "batch-1", "state": "COMPLETED", "result": "SUCCESS"}
            )
        if path.endswith("/finalize/job"):
            return httpx.Response(200, json={"id": "fin-1"})
        if path.endswith("/jobs/fin-1"):
            return httpx.Response(200, json={"id": "fin-1", "state": "RUNNING"})
        return httpx.Response(500)

    clock = FakeClock()

    def jump_sleep(seconds: float) -> None:
        clock.now += max(seconds, 300.0)

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
        monotonic_clock=clock,
        sleeper=jump_sleep,
    )
    result = execute_collibra_plan(
        adapter,
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert result.success is False
    assert "timeout" in (result.error or "")


def test_import_v2_does_not_call_synchronize_or_finalize() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if "/import/synchronize" in path:
            raise AssertionError("import_v2 must not call synchronize")
        if request.method == "GET" and path.endswith("/assets"):
            return httpx.Response(200, json={"results": [], "total": 0})
        if path.endswith("/import/json-job"):
            return httpx.Response(200, json={"id": "job-1"})
        if path.endswith("/jobs/job-1"):
            return httpx.Response(
                200, json={"id": "job-1", "state": "COMPLETED", "result": "SUCCESS"}
            )
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="import_v2",
    )
    assert result.success is True
    paths = [urlparse(str(item.url)).path for item in requests]
    assert "/rest/2.0/import/json-job" in paths
    assert all("/import/synchronize" not in path for path in paths)


def test_derived_sync_id_stable_for_source_and_endpoint() -> None:
    left = derive_synchronization_id(
        provider="collibra",
        source_name="governance-demo",
        endpoint="https://collibra.example.invalid",
    )
    right = derive_synchronization_id(
        provider="collibra",
        source_name="governance-demo",
        endpoint="https://collibra.example.invalid",
    )
    other_source = derive_synchronization_id(
        provider="collibra",
        source_name="other-source",
        endpoint="https://collibra.example.invalid",
    )
    other_endpoint = derive_synchronization_id(
        provider="collibra",
        source_name="governance-demo",
        endpoint="https://other.example.invalid",
    )
    assert left == right
    assert UUID(left).version == 5
    assert left != other_source
    assert left != other_endpoint


def test_mapping_does_not_change_derived_sync_id() -> None:
    settings = _settings(collibra_synchronization_id="")
    first = effective_synchronization_id(settings)
    second = effective_synchronization_id(settings)
    assert first == second
    assert first not in mock_mapping_config().attribute_type_refs.values()


def test_literal_and_env_override_bind_target_identity() -> None:
    derived = build_target_context_projection(_settings(collibra_synchronization_id=""))
    literal_a = build_target_context_projection(_settings(collibra_synchronization_id=UUID_A))
    literal_b = build_target_context_projection(_settings(collibra_synchronization_id=UUID_B))
    same_again = build_target_context_projection(_settings(collibra_synchronization_id=UUID_A))
    assert derived["execution"] == "sync_v2"
    assert "effective_synchronization_id" in derived
    assert literal_a["effective_synchronization_id"] == str(UUID(UUID_A))
    assert target_context_identity(literal_a) == target_context_identity(same_again)
    assert target_context_identity(literal_a) != target_context_identity(literal_b)
    assert target_context_identity(derived) != target_context_identity(literal_a)
    public = target_context_public(literal_a)
    assert public == {"provider": "collibra", "mode": "live"}
    assert "effective_synchronization_id" not in public
    assert "execution" not in public
    dumped = str(literal_a)
    assert "secret-token-value-xyz" not in dumped
    assert "client_secret" not in dumped


def test_core_rest_and_import_v2_omit_sync_id() -> None:
    core = build_target_context_projection(_settings(collibra_execution_mode="core_rest"))
    imported = build_target_context_projection(_settings(collibra_execution_mode="import_v2"))
    assert "effective_synchronization_id" not in core
    assert "execution" not in core
    assert imported["execution"] == "import_v2"
    assert "effective_synchronization_id" not in imported


def test_sync_v2_dry_run_zero_posts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=False,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert result.dry_run is True
    assert result.success is True
    assert requests == []


def test_live_finalize_rejects_remove_before_http() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    adapter = _sync_adapter(handler)
    with pytest.raises(CollibraAdapterError, match="forbidden"):
        adapter.submit_sync_finalize(UUID_A, strategy="REMOVE_RESOURCES")
    assert requests == []
