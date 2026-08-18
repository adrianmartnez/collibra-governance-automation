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
    ImportCollisionError,
    LiveCollibraAdapter,
    SyncAction,
    SyncActionType,
    SyncLifecycleResult,
    SyncObjectKind,
    SyncPlan,
    execute_collibra_plan,
    mock_mapping_config,
    prove_import_create_identifiers_absent,
)
from governance.integrations.collibra.batching import partition_document
from governance.integrations.collibra.import_api import (
    IMPORT_MULTIPART_FIELDS,
    compile_import_document,
)
from governance.integrations.collibra.jobs import (
    IMPORT_SUBMISSION_UNCERTAIN,
    JOB_OBSERVATION_FAILURE,
    SYNC_BATCH_SUBMISSION_UNCERTAIN,
    classify_job,
    is_remote_terminal,
    poll_until_terminal,
    sanitize_import_error_summary,
    submission_state_as_bool,
    timeout_view,
    validate_finite_poll_seconds,
)
from governance.integrations.collibra.synchronization import (
    derive_synchronization_id,
    effective_synchronization_id,
    require_ignore_strategy,
)
from governance.plans.sync_lifecycle_result import (
    build_sync_lifecycle_sync_payload,
    format_sync_lifecycle_result_human,
)
from governance.plans.target_context import (
    build_target_context_projection,
    target_context_public,
)

UUID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _empty_assets_response() -> httpx.Response:
    return httpx.Response(200, json={"results": [], "total": 0})


def _maybe_empty_assets(request: httpx.Request) -> httpx.Response | None:
    path = urlparse(str(request.url)).path
    if request.method == "GET" and path.endswith("/assets"):
        return _empty_assets_response()
    return None


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
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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
    assert result.finalization_job_id == "fin-1"
    assert result.batch_job_ids == ("batch-1",)
    methods_paths = [(item.method, urlparse(str(item.url)).path) for item in requests]
    assert ("GET", "/rest/2.0/assets") in methods_paths
    assert any(
        method == "POST" and path.endswith(f"/import/synchronize/{UUID_A}/batch/json-job")
        for method, path in methods_paths
    )
    assert ("GET", "/rest/2.0/jobs/batch-1") in methods_paths
    assert any(path.endswith("/finalize/job") for _method, path in methods_paths)
    assert ("GET", "/rest/2.0/jobs/fin-1") in methods_paths


def test_batch_failure_never_sends_finalize() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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
    assert result.finalization_submission_state == "unknown"
    assert result.finalization_submitted is None
    assert result.batch_job_ids == ("batch-1",)
    assert result.finalization_job_id is None
    assert "uncertain" in (result.error or "")
    assert finalize_posts == 1


def test_finalize_timeout_is_not_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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


def _update_only_plan() -> SyncPlan:
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
                action_type=SyncActionType.UPDATE,
                object_kind=SyncObjectKind.ASSET,
                local_id=asset.local_id,
                remote_id="remote-1",
                reason="attr",
                desired_asset=asset,
                changed_fields=("managed_attributes",),
            ),
        )
    )


def test_sync_v2_collision_empty_occupancy_allows_batch() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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
            return httpx.Response(
                200, json={"id": "fin-1", "state": "COMPLETED", "result": "SUCCESS"}
            )
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert isinstance(result, SyncLifecycleResult)
    assert result.success is True
    assert any(
        request.method == "GET" and urlparse(str(request.url)).path.endswith("/assets")
        for request in requests
    )
    assert any(
        request.method == "POST" and "/batch/json-job" in urlparse(str(request.url)).path
        for request in requests
    )


def test_sync_v2_collision_unmanaged_occupant_zero_batch() -> None:
    requests: list[httpx.Request] = []
    config = mock_mapping_config()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "unmanaged-1",
                            "name": "demo",
                            "domain": {"id": config.domain_ref},
                        }
                    ],
                    "total": 1,
                },
            )
        if "/batch/" in path or path.endswith("/finalize/job"):
            raise AssertionError("batch/finalize must not run on collision")
        return httpx.Response(500)

    with pytest.raises(ImportCollisionError, match="collides"):
        execute_collibra_plan(
            _sync_adapter(handler),
            _create_plan(),
            config,
            apply=True,
            execution_mode="sync_v2",
            synchronization_id=UUID_A,
        )
    assert not any("/batch/" in urlparse(str(item.url)).path for item in requests)
    assert not any(urlparse(str(item.url)).path.endswith("/finalize/job") for item in requests)


def test_sync_v2_collision_lookup_failure_zero_batch() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            return httpx.Response(503, json={"error": "unavailable"})
        if "/batch/" in path or path.endswith("/finalize/job"):
            raise AssertionError("batch/finalize must not run on lookup failure")
        return httpx.Response(500)

    with pytest.raises(ImportCollisionError, match="collision check failed"):
        execute_collibra_plan(
            _sync_adapter(handler),
            _create_plan(),
            mock_mapping_config(),
            apply=True,
            execution_mode="sync_v2",
            synchronization_id=UUID_A,
        )
    assert not any("/batch/" in urlparse(str(item.url)).path for item in requests)


def test_sync_v2_collision_uses_exact_whitespace_identifier() -> None:
    config = mock_mapping_config()
    spaced = " demo "
    asset = CollibraAssetSpec(
        local_id="tbl:spaced",
        name=spaced,
        display_name=spaced,
        asset_type_ref=config.asset_type_refs["table"],
        domain_ref=config.domain_ref,
        attributes=(CollibraAttributeSpec(config.attribute_type_refs["local_id"], "tbl:spaced"),),
    )
    plan = SyncPlan(
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
    seen_names: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            seen_names.append(str(request.url.params.get("name") or ""))
            return _empty_assets_response()
        return httpx.Response(500)

    prove_import_create_identifiers_absent(_sync_adapter(handler), plan)
    assert seen_names == [spaced]


def test_sync_v2_update_only_skips_collision_get() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            raise AssertionError("UPDATE-only sync_v2 must not collision-check")
        if path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if path.endswith("/jobs/batch-1"):
            return httpx.Response(
                200, json={"id": "batch-1", "state": "COMPLETED", "result": "SUCCESS"}
            )
        if path.endswith("/finalize/job"):
            return httpx.Response(200, json={"id": "fin-1"})
        if path.endswith("/jobs/fin-1"):
            return httpx.Response(
                200, json={"id": "fin-1", "state": "COMPLETED", "result": "SUCCESS"}
            )
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _update_only_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert result.success is True
    assert not any(
        request.method == "GET" and urlparse(str(request.url)).path.endswith("/assets")
        for request in requests
    )


def test_sync_v2_batch_failure_preserves_batch_job_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
        path = urlparse(str(request.url)).path
        if path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if path.endswith("/jobs/batch-1"):
            return httpx.Response(
                200,
                json={"id": "batch-1", "state": "COMPLETED", "result": "COMPLETED_WITH_ERROR"},
            )
        if path.endswith("/import/results/batch-1/errors"):
            return httpx.Response(200, json={"results": [{"row": 1}, {"row": 2}], "total": 2})
        if path.endswith("/finalize/job"):
            raise AssertionError("finalize must not run")
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert isinstance(result, SyncLifecycleResult)
    assert result.success is False
    assert result.batch_job_ids == ("batch-1",)
    assert result.finalization_job_id is None
    assert result.import_error_summary == {"error_count": 2}
    dumped = str(result)
    assert "secret-token-value-xyz" not in dumped


def test_sync_v2_finalize_failure_preserves_both_job_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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
            return httpx.Response(
                200, json={"id": "fin-1", "state": "COMPLETED", "result": "FAILURE"}
            )
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
    assert result.batch_job_ids == ("batch-1",)
    assert result.finalization_job_id == "fin-1"
    assert result.finalization_submission_state == "submitted"
    assert result.finalization_submitted is True


def test_finalize_request_is_multipart_with_ignore() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/finalize/job"):
            content_type = request.headers.get("content-type", "")
            assert content_type.startswith("multipart/form-data")
            assert "boundary=" in content_type
            body = request.content.decode("utf-8", errors="replace")
            assert "finalizationStrategy" in body
            assert "IGNORE" in body
            assert "REMOVE_RESOURCES" not in body
            assert "CHANGE_STATUS" not in body
            assert "missingAssetStatusId" not in body
            return httpx.Response(200, json={"id": "fin-1"})
        return httpx.Response(500)

    adapter = _sync_adapter(handler)
    adapter.submit_sync_finalize(UUID_A, strategy="IGNORE")
    assert len(requests) == 1


def test_custom_job_poll_settings_on_adapter() -> None:
    adapter = LiveCollibraAdapter.from_settings(
        _settings(
            collibra_job_poll_interval_seconds=2.5,
            collibra_job_poll_timeout_seconds=12.0,
        ),
        mock_mapping_config(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        monotonic_clock=FakeClock(),
        sleeper=lambda _seconds: None,
    )
    assert adapter._job_poll_interval_seconds == 2.5
    assert adapter._job_poll_timeout_seconds == 12.0


def test_custom_poll_timeout_blocks_finalize() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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
        clock.now += max(seconds, 5.0)

    adapter = LiveCollibraAdapter.from_settings(
        _settings(
            collibra_job_poll_interval_seconds=1.0,
            collibra_job_poll_timeout_seconds=3.0,
        ),
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
    assert result.finalization_job_id == "fin-1"


def test_changing_poll_policy_does_not_change_target_context_identity() -> None:
    base = build_target_context_projection(_settings(collibra_synchronization_id=UUID_A))
    slower = build_target_context_projection(
        _settings(
            collibra_synchronization_id=UUID_A,
            collibra_job_poll_interval_seconds=0.5,
            collibra_job_poll_timeout_seconds=600.0,
        )
    )
    assert target_context_identity(base) == target_context_identity(slower)


@pytest.mark.parametrize(
    ("payload", "terminal"),
    [
        ({"state": "WAITING"}, False),
        ({"state": "RUNNING"}, False),
        ({"state": "CANCELING"}, False),
        ({"state": "CANCELED"}, True),
        ({"state": "ERROR"}, True),
        ({"state": "COMPLETED", "result": "SUCCESS"}, True),
        ({"state": "COMPLETED", "result": "FAILURE"}, True),
        ({"state": "COMPLETED", "result": "NOT_SET"}, True),
        ({"state": "NEW_STATE"}, False),
        ("not-a-job", False),
    ],
)
def test_is_remote_terminal_semantics(payload: object, terminal: bool) -> None:
    view = classify_job(payload)
    assert is_remote_terminal(view) is terminal


def test_timeout_after_running_is_not_remote_terminal() -> None:
    running = classify_job({"id": "j1", "state": "RUNNING"})
    timed_out = timeout_view(running)
    assert timed_out.normalized_state == "timeout"
    assert is_remote_terminal(timed_out) is False


def test_sanitize_import_error_summary_prefers_total_over_page() -> None:
    summary = sanitize_import_error_summary(
        {"total": 1500, "results": [{"row": index} for index in range(1000)]}
    )
    assert summary == {"error_count": 1500}


def test_sanitize_import_error_summary_total_zero() -> None:
    assert sanitize_import_error_summary({"total": 0, "results": [{"row": 1}]}) == {
        "error_count": 0
    }


def test_sanitize_import_error_summary_falls_back_to_results_length() -> None:
    assert sanitize_import_error_summary({"results": [{}, {}, {}]}) == {"error_count": 3}


def test_sanitize_import_error_summary_never_includes_raw_payload() -> None:
    summary = sanitize_import_error_summary(
        {
            "total": 1,
            "results": [
                {
                    "errorMessage": "super-secret-value",
                    "command": {"identifier": {"name": "orders"}},
                }
            ],
        }
    )
    dumped = str(summary)
    assert summary == {"error_count": 1}
    assert "super-secret" not in dumped
    assert "orders" not in dumped


def test_import_poll_get_failure_preserves_job_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            return _empty_assets_response()
        if path.endswith("/import/json-job"):
            return httpx.Response(200, json={"id": "job-123"})
        if path.endswith("/jobs/job-123"):
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="import_v2",
    )
    assert result.success is False
    assert result.job_id == "job-123"
    assert result.submission_state == "submitted"
    assert result.error == JOB_OBSERVATION_FAILURE
    assert result.job is None
    assert not any("/import/synchronize" in urlparse(str(item.url)).path for item in requests)


def test_sync_batch_poll_get_failure_preserves_batch_id_no_finalize() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
        path = urlparse(str(request.url)).path
        if path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if path.endswith("/jobs/batch-1"):
            return httpx.Response(503, json={"error": "unavailable"})
        if path.endswith("/finalize/job"):
            raise AssertionError("finalize must not run")
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
    assert result.batch_job_ids == ("batch-1",)
    assert result.finalization_submission_state == "not_attempted"
    assert result.error == JOB_OBSERVATION_FAILURE
    assert not any(item.url.path.endswith("/finalize/job") for item in requests)


def test_sync_finalize_poll_get_failure_preserves_both_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
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
            return httpx.Response(503, json={"error": "unavailable"})
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
    assert result.batch_job_ids == ("batch-1",)
    assert result.finalization_job_id == "fin-1"
    assert result.finalization_submission_state == "submitted"
    assert result.error == JOB_OBSERVATION_FAILURE


def test_sync_finalize_write_timeout_preserves_batch_unknown_submission() -> None:
    finalize_posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal finalize_posts
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
        path = urlparse(str(request.url)).path
        if path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if path.endswith("/jobs/batch-1"):
            return httpx.Response(
                200, json={"id": "batch-1", "state": "COMPLETED", "result": "SUCCESS"}
            )
        if path.endswith("/finalize/job"):
            finalize_posts += 1
            raise httpx.WriteTimeout("write timeout")
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
    assert result.batch_job_ids == ("batch-1",)
    assert result.finalization_submission_state == "unknown"
    assert result.finalization_submitted is None
    assert result.finalization_job_id is None
    assert finalize_posts == 1


def test_submission_state_bool_aliases_do_not_collapse_unknown() -> None:
    assert submission_state_as_bool("submitted") is True
    assert submission_state_as_bool("not_attempted") is False
    assert submission_state_as_bool("unknown") is None


def test_import_submission_unknown_bool_alias_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
        path = urlparse(str(request.url)).path
        if path.endswith("/import/json-job"):
            return httpx.Response(200, json={"name": "missing-id"})
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="import_v2",
    )
    assert result.submission_state == "unknown"
    assert result.submitted is None
    assert result.job_id is None


def test_import_submit_write_timeout_returns_unknown_submission() -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
        path = urlparse(str(request.url)).path
        if path.endswith("/import/json-job"):
            posts += 1
            raise httpx.WriteTimeout("write timeout")
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="import_v2",
    )
    assert posts == 1
    assert result.submission_state == "unknown"
    assert result.submitted is None
    assert result.job_id is None
    assert result.success is False
    assert result.error == IMPORT_SUBMISSION_UNCERTAIN


def test_sync_batch_submit_write_timeout_unknown_no_finalize() -> None:
    batch_posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal batch_posts
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
        path = urlparse(str(request.url)).path
        if path.endswith("/batch/json-job"):
            batch_posts += 1
            raise httpx.WriteTimeout("write timeout")
        if path.endswith("/finalize/job"):
            raise AssertionError("finalize must not run")
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert batch_posts == 1
    assert result.batch_submission_state == "unknown"
    assert result.batch_job_ids == ()
    assert result.finalization_submission_state == "not_attempted"
    assert result.success is False
    assert result.error == SYNC_BATCH_SUBMISSION_UNCERTAIN


def test_sync_batch_missing_id_unknown_no_finalize() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        empty = _maybe_empty_assets(request)
        if empty is not None:
            return empty
        path = urlparse(str(request.url)).path
        if path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"name": "missing-id"})
        if path.endswith("/finalize/job"):
            raise AssertionError("finalize must not run")
        return httpx.Response(500)

    result = execute_collibra_plan(
        _sync_adapter(handler),
        _create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
    )
    assert result.batch_submission_state == "unknown"
    assert result.batch_job_ids == ()
    assert not any(item.url.path.endswith("/finalize/job") for item in requests)


def test_sync_lifecycle_payload_preserves_batch_id_without_job_view() -> None:
    from governance.integrations.collibra.jobs import make_batch_lifecycle_record

    plan = _create_plan()
    document = compile_import_document(plan, mock_mapping_config())
    batch = partition_document(document, max_resources=50_000)[0]
    record = make_batch_lifecycle_record(
        0,
        batch,
        submission_state="submitted",
        job_id="batch-1",
        observation_error=JOB_OBSERVATION_FAILURE,
    )
    result = SyncLifecycleResult(
        plan=plan,
        document=document,
        dry_run=False,
        synchronization_id=UUID_A,
        batch_lifecycle=(record,),
        finalization_submission_state="not_attempted",
        finalization_job_id=None,
        finalization_job=None,
        success=False,
        applied_count=0,
        unchanged_count=0,
        error=JOB_OBSERVATION_FAILURE,
    )
    payload = build_sync_lifecycle_sync_payload(mode="live", result=result)
    assert payload["result_schema"] == "governance-sync-lifecycle-result"
    assert payload["batch_job_ids"] == ["batch-1"]
    assert payload["batch_lifecycle"][0]["job_id"] == "batch-1"
    assert payload["batch_lifecycle"][0]["observation_error"] == JOB_OBSERVATION_FAILURE
    human = format_sync_lifecycle_result_human(payload)
    assert "batch_job_id_0=batch-1" in human


def test_poll_until_terminal_rejects_non_finite_policy() -> None:
    clock = FakeClock()
    with pytest.raises(ValueError, match="finite positive"):
        poll_until_terminal(
            lambda _job_id: {"id": "j1", "state": "COMPLETED", "result": "SUCCESS"},
            "j1",
            monotonic_clock=clock,
            sleeper=clock.sleep,
            interval_seconds=float("nan"),
            timeout_seconds=5.0,
        )


def test_validate_finite_poll_seconds_accepts_valid_values() -> None:
    assert validate_finite_poll_seconds(1.5, "interval_seconds") == 1.5
