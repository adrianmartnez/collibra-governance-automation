"""Conservative Import/sync batching tests."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import pytest

from governance.config import Settings
from governance.integrations.collibra import (
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
from governance.integrations.collibra.batching import (
    HARD_MAX_ADDITIONAL_CHARACTERISTICS,
    HARD_MAX_RESOURCES,
    BatchingError,
    CommandCounts,
    partition_counts,
    partition_document,
)
from governance.integrations.collibra.import_api import compile_import_document

UUID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


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
    }
    base.update(overrides)
    return Settings(**base)


def _asset(local_id: str, name: str) -> CollibraAssetSpec:
    config = mock_mapping_config()
    return CollibraAssetSpec(
        local_id=local_id,
        name=name,
        display_name=name,
        asset_type_ref=config.asset_type_refs["table"],
        domain_ref=config.domain_ref,
        attributes=(CollibraAttributeSpec(config.attribute_type_refs["local_id"], local_id),),
    )


def _two_create_plan() -> SyncPlan:
    a = _asset("tbl:a", "a")
    b = _asset("tbl:b", "b")
    return SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id=a.local_id,
                reason="a",
                desired_asset=a,
            ),
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id=b.local_id,
                reason="b",
                desired_asset=b,
            ),
        )
    )


def test_resource_boundaries_without_materializing_large_payloads() -> None:
    max_r = HARD_MAX_RESOURCES
    max_a = HARD_MAX_ADDITIONAL_CHARACTERISTICS
    assert partition_counts(
        [CommandCounts(49_999, 0, 0)],
        max_resources=max_r,
        max_additional_characteristics=max_a,
    ) == ((0,),)
    assert partition_counts(
        [CommandCounts(50_000, 0, 0)],
        max_resources=max_r,
        max_additional_characteristics=max_a,
    ) == ((0,),)
    with pytest.raises(BatchingError, match="exceeds the batch ceiling"):
        partition_counts(
            [CommandCounts(50_001, 0, 0)],
            max_resources=max_r,
            max_additional_characteristics=max_a,
        )


def test_additional_characteristic_boundaries() -> None:
    max_r = HARD_MAX_RESOURCES
    max_a = HARD_MAX_ADDITIONAL_CHARACTERISTICS
    assert partition_counts(
        [CommandCounts(1, 499_999, 0)],
        max_resources=max_r,
        max_additional_characteristics=max_a,
    ) == ((0,),)
    assert partition_counts(
        [CommandCounts(1, 0, 500_000)],
        max_resources=max_r,
        max_additional_characteristics=max_a,
    ) == ((0,),)
    with pytest.raises(BatchingError, match="exceeds the batch ceiling"):
        partition_counts(
            [CommandCounts(1, 500_001, 0)],
            max_resources=max_r,
            max_additional_characteristics=max_a,
        )


def test_greedy_split_and_reordered_counts() -> None:
    groups = partition_counts(
        [CommandCounts(1, 0, 0), CommandCounts(1, 0, 0), CommandCounts(1, 0, 0)],
        max_resources=2,
        max_additional_characteristics=HARD_MAX_ADDITIONAL_CHARACTERISTICS,
    )
    assert groups == ((0, 1), (2,))
    reversed_groups = partition_counts(
        [CommandCounts(1, 0, 0), CommandCounts(1, 0, 0), CommandCounts(1, 0, 0)][::-1],
        max_resources=2,
        max_additional_characteristics=HARD_MAX_ADDITIONAL_CHARACTERISTICS,
    )
    assert reversed_groups == ((0, 1), (2,))


def test_configured_ceiling_cannot_exceed_hard_max() -> None:
    with pytest.raises(ValueError, match="hard maxima"):
        _settings(collibra_batch_max_resources=HARD_MAX_RESOURCES + 1)
    with pytest.raises(ValueError, match="hard maxima"):
        _settings(
            collibra_batch_max_additional_characteristics=HARD_MAX_ADDITIONAL_CHARACTERISTICS + 1
        )


def test_import_v2_stop_on_failure_and_no_finalize() -> None:
    requests: list[httpx.Request] = []
    job_ids = iter(["job-1", "job-2"])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if "/import/synchronize" in path or path.endswith("/finalize/job"):
            raise AssertionError("import_v2 must not finalize or synchronize")
        if request.method == "POST" and path.endswith("/import/json-job"):
            return httpx.Response(200, json={"id": next(job_ids)})
        if path.endswith("/jobs/job-1"):
            return httpx.Response(
                200, json={"id": "job-1", "state": "COMPLETED", "result": "FAILURE"}
            )
        return httpx.Response(500)

    clock = FakeClock()
    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
        monotonic_clock=clock,
        sleeper=clock.sleep,
    )
    result = execute_collibra_plan(
        adapter,
        _two_create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="import_v2",
        max_resources=1,
        max_additional_characteristics=HARD_MAX_ADDITIONAL_CHARACTERISTICS,
    )
    assert result.success is False
    posts = [item for item in requests if item.method == "POST"]
    assert len(posts) == 1
    assert urlparse(str(posts[0].url)).path == "/rest/2.0/import/json-job"


def test_sync_v2_stop_on_failure_skips_finalize() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if path.endswith("/finalize/job"):
            raise AssertionError("failed batch must not finalize")
        if request.method == "POST" and path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": "batch-1"})
        if path.endswith("/jobs/batch-1"):
            return httpx.Response(
                200, json={"id": "batch-1", "state": "COMPLETED", "result": "FAILURE"}
            )
        return httpx.Response(500)

    clock = FakeClock()
    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
        monotonic_clock=clock,
        sleeper=clock.sleep,
    )
    result = execute_collibra_plan(
        adapter,
        _two_create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
        max_resources=1,
        max_additional_characteristics=HARD_MAX_ADDITIONAL_CHARACTERISTICS,
    )
    assert result.success is False
    posts = [item for item in requests if item.method == "POST"]
    assert len(posts) == 1
    assert urlparse(str(posts[0].url)).path.endswith("/batch/json-job")


def test_sync_v2_finalizes_only_after_all_batches_succeed() -> None:
    requests: list[httpx.Request] = []
    batch_ids = iter(["batch-1", "batch-2"])
    jobs = {
        "batch-1": {"id": "batch-1", "state": "COMPLETED", "result": "SUCCESS"},
        "batch-2": {"id": "batch-2", "state": "COMPLETED", "result": "SUCCESS"},
        "fin-1": {"id": "fin-1", "state": "COMPLETED", "result": "SUCCESS"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "POST" and path.endswith("/batch/json-job"):
            return httpx.Response(200, json={"id": next(batch_ids)})
        if request.method == "POST" and path.endswith("/finalize/job"):
            body = request.content.decode("utf-8", errors="replace")
            assert "IGNORE" in body
            assert "REMOVE_RESOURCES" not in body
            return httpx.Response(200, json={"id": "fin-1"})
        if request.method == "GET" and "/jobs/" in path:
            job_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=jobs[job_id])
        return httpx.Response(500)

    clock = FakeClock()
    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
        monotonic_clock=clock,
        sleeper=clock.sleep,
    )
    result = execute_collibra_plan(
        adapter,
        _two_create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode="sync_v2",
        synchronization_id=UUID_A,
        max_resources=1,
        max_additional_characteristics=HARD_MAX_ADDITIONAL_CHARACTERISTICS,
    )
    assert result.success is True
    posts = [urlparse(str(item.url)).path for item in requests if item.method == "POST"]
    assert len([path for path in posts if path.endswith("/batch/json-job")]) == 2
    assert len([path for path in posts if path.endswith("/finalize/job")]) == 1
    assert posts[-1].endswith("/finalize/job")


def test_low_ceiling_partition_keeps_command_order() -> None:
    document = compile_import_document(_two_create_plan(), mock_mapping_config())
    batches = partition_document(document, max_resources=1, max_additional_characteristics=100)
    assert len(batches) == 2
    names = [batch.to_list()[0]["identifier"]["name"] for batch in batches]
    original = [command["identifier"]["name"] for command in document.to_list()]
    assert names == original
