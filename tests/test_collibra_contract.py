"""LiveCollibraAdapter contract tests against a localhost stdlib HTTP server."""

from __future__ import annotations

import httpx
import pytest

from governance.config import Settings
from governance.integrations.collibra import (
    CollibraAdapterError,
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraAuthError,
    CollibraDesiredState,
    LiveCollibraAdapter,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
    execute_collibra_plan,
    mock_mapping_config,
)
from governance.integrations.collibra.batching import HARD_MAX_ADDITIONAL_CHARACTERISTICS
from governance.integrations.collibra.import_api import IMPORT_MULTIPART_FIELDS
from tests.support.collibra_contract_server import (
    CONTRACT_CLIENT_SECRET,
    CONTRACT_TOKEN,
    CollibraContractServer,
)

pytestmark = pytest.mark.collibra_contract

UUID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _two_create_plan() -> SyncPlan:
    config = mock_mapping_config()

    def asset(local_id: str, name: str) -> CollibraAssetSpec:
        return CollibraAssetSpec(
            local_id=local_id,
            name=name,
            display_name=name,
            asset_type_ref=config.asset_type_refs["table"],
            domain_ref=config.domain_ref,
            attributes=(CollibraAttributeSpec(config.attribute_type_refs["local_id"], local_id),),
        )

    first = asset("tbl:a", "a")
    second = asset("tbl:b", "b")
    return SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id=first.local_id,
                reason="a",
                desired_asset=first,
            ),
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id=second.local_id,
                reason="b",
                desired_asset=second,
            ),
        )
    )


def _settings(base_url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "governance_demo",
        "postgres_user": "postgres",
        "postgres_password": "postgres",
        "postgres_source_name": "governance-demo",
        "inventory_output_path": "artifacts/metadata-inventory.json",
        "collibra_mode": "live",
        "collibra_base_url": base_url,
        "collibra_client_id": "contract-client",
        "collibra_client_secret": CONTRACT_CLIENT_SECRET,
        "collibra_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return Settings(**values)


def _adapter(server: CollibraContractServer, **overrides: object) -> LiveCollibraAdapter:
    clock = FakeClock()
    return LiveCollibraAdapter.from_settings(
        _settings(server.base_url, **overrides),
        mock_mapping_config(),
        monotonic_clock=clock,
        sleeper=clock.sleep,
        page_size=1,
    )


def _assert_no_secrets(server: CollibraContractServer) -> None:
    blob = str(server.sanitized_requests)
    assert CONTRACT_TOKEN not in blob
    assert CONTRACT_CLIENT_SECRET not in blob


def _assert_import_safety(server: CollibraContractServer) -> None:
    bodies = [
        item["body"]
        for item in server.sanitized_requests
        if item["method"] == "POST" and item["path"].endswith("/json-job")
    ]
    assert bodies
    for body in bodies:
        for key, value in IMPORT_MULTIPART_FIELDS.items():
            assert key in body
            assert value in body


def _execute(
    adapter: LiveCollibraAdapter,
    *,
    execution_mode: str,
    synchronization_id: str | None = None,
) -> object:
    return execute_collibra_plan(
        adapter,
        _two_create_plan(),
        mock_mapping_config(),
        apply=True,
        execution_mode=execution_mode,
        synchronization_id=synchronization_id,
        max_resources=10,
        max_additional_characteristics=HARD_MAX_ADDITIONAL_CHARACTERISTICS,
    )


def test_native_oauth_application_info_and_import_safety() -> None:
    with CollibraContractServer() as server:
        adapter = _adapter(server)
        info = adapter._request("GET", "/rest/2.0/application/info")
        assert info["version"] == "contract-test"
        result = _execute(adapter, execution_mode="import_v2")
        assert result.success is True
        errors = adapter.get_import_errors("job-import")
        assert errors == {"error_count": 0}
        paths = [item["path"] for item in server.sanitized_requests]
        assert any(path.endswith("/oauth/v2/token") for path in paths)
        assert any(path.endswith("/import/json-job") for path in paths)
        assert any(path.endswith("/import/results/job-import/errors") for path in paths)
        assert all("/import/synchronize/" not in path or "/batch/" in path for path in paths)
        _assert_import_safety(server)
        _assert_no_secrets(server)


def test_external_idp_post_and_basic() -> None:
    with CollibraContractServer() as server:
        post_adapter = _adapter(
            server,
            collibra_token_url=server.token_url,
            collibra_oauth_client_auth="client_secret_post",
        )
        post_adapter._request("GET", "/rest/2.0/application/info")
        basic_adapter = _adapter(
            server,
            collibra_token_url=server.token_url,
            collibra_oauth_client_auth="client_secret_basic",
        )
        basic_adapter._request("GET", "/rest/2.0/application/info")
        token_paths = [
            item["path"]
            for item in server.sanitized_requests
            if item["path"].endswith("/idp/token")
        ]
        assert len(token_paths) >= 2
        _assert_no_secrets(server)


def test_core_rest_writes() -> None:
    with CollibraContractServer() as server:
        adapter = _adapter(server)
        result = _execute(adapter, execution_mode="core_rest")
        assert result.success is True
        methods = {(item["method"], item["path"]) for item in server.sanitized_requests}
        assert ("POST", "/rest/2.0/assets") in methods
        assert ("POST", "/rest/2.0/attributes") in methods
        assert all(not path.endswith("/json-job") for _, path in methods)
        _assert_no_secrets(server)


def test_pagination_auth_failure_and_expired_token() -> None:
    with CollibraContractServer(scenario="auth_failure") as server:
        adapter = _adapter(server)
        with pytest.raises(CollibraAuthError):
            adapter._request("GET", "/rest/2.0/application/info")
    with CollibraContractServer(scenario="expired_token") as server:
        config = mock_mapping_config()
        server.assets = [
            {
                "id": "a1",
                "name": "one",
                "domain": {"id": config.domain_ref},
                "type": {"id": config.asset_type_refs["table"]},
            },
            {
                "id": "a2",
                "name": "two",
                "domain": {"id": config.domain_ref},
                "type": {"id": config.asset_type_refs["table"]},
            },
        ]
        adapter = _adapter(server)
        adapter.read_remote_state(CollibraDesiredState(assets=()))
        assert server.token_posts >= 2
        asset_gets = [
            item
            for item in server.sanitized_requests
            if item["method"] == "GET" and item["path"].endswith("/assets")
        ]
        assert any("offset=1" in item["query"] for item in asset_gets)
        _assert_no_secrets(server)


@pytest.mark.parametrize("scenario", ["retry_after_delta", "retry_after_http_date", "status_5xx"])
def test_retry_after_and_5xx(scenario: str) -> None:
    with CollibraContractServer(scenario=scenario) as server:
        adapter = _adapter(server)
        adapter.read_remote_state(CollibraDesiredState(assets=()))
        assert any(item["path"].endswith("/assets") for item in server.sanitized_requests)


def test_connection_reset_and_malformed_json() -> None:
    with CollibraContractServer(scenario="conn_reset") as server:
        adapter = _adapter(server)
        with pytest.raises((CollibraAdapterError, httpx.HTTPError)):
            adapter.read_remote_state(CollibraDesiredState(assets=()))
    with CollibraContractServer(scenario="malformed") as server:
        adapter = _adapter(server)
        with pytest.raises(CollibraAdapterError, match="malformed"):
            adapter._request("GET", "/rest/2.0/application/info")


def test_job_delay_and_documented_outcomes() -> None:
    with CollibraContractServer(scenario="job_delay") as server:
        adapter = _adapter(server)
        result = _execute(adapter, execution_mode="import_v2")
        assert result.success is True
        assert server._job_polls.get("job-import", 0) >= 3

    for scenario in (
        "job_error",
        "job_completed_with_error",
        "job_canceling",
        "job_aborted",
        "job_unknown",
    ):
        with CollibraContractServer(scenario=scenario) as server:
            adapter = _adapter(server)
            outcome = _execute(adapter, execution_mode="import_v2")
            assert outcome.success is False
            if scenario == "job_canceling":
                assert server._job_polls.get("job-import", 0) >= 2


def test_sync_finalize_gates_and_no_duplicate() -> None:
    with CollibraContractServer() as server:
        adapter = _adapter(server)
        result = _execute(adapter, execution_mode="sync_v2", synchronization_id=UUID_A)
        assert result.success is True
        assert server.finalize_posts == 1
        _assert_import_safety(server)
        paths = [item["path"] for item in server.sanitized_requests]
        assert any(path.endswith("/batch/json-job") for path in paths)
        assert all(
            "/json-job" not in path or "/batch/" in path
            for path in paths
            if "/synchronize/" in path
        )

    with CollibraContractServer(scenario="finalize_malformed") as server:
        adapter = _adapter(server)
        outcome = _execute(adapter, execution_mode="sync_v2", synchronization_id=UUID_A)
        assert outcome.success is False
        assert server.finalize_posts == 1

    for scenario in ("finalize_failure", "finalize_unknown"):
        with CollibraContractServer(scenario=scenario) as server:
            adapter = _adapter(server)
            outcome = _execute(adapter, execution_mode="sync_v2", synchronization_id=UUID_A)
            assert outcome.success is False
            assert server.finalize_posts == 1

    clock = FakeClock()

    def jump(seconds: float) -> None:
        clock.now += max(seconds, 300.0)

    with CollibraContractServer(scenario="finalize_timeout") as server:
        adapter = LiveCollibraAdapter.from_settings(
            _settings(server.base_url),
            mock_mapping_config(),
            monotonic_clock=clock,
            sleeper=jump,
        )
        outcome = _execute(adapter, execution_mode="sync_v2", synchronization_id=UUID_A)
        assert outcome.success is False
        assert "timeout" in (outcome.error or "")
        assert server.finalize_posts == 1


def test_server_rejects_non_ignore_finalize_and_combined_endpoint() -> None:
    with CollibraContractServer() as server:
        finalize = httpx.post(
            f"{server.base_url}/rest/2.0/import/synchronize/{UUID_A}/finalize/job",
            data={"finalizationStrategy": "REMOVE_RESOURCES"},
            timeout=2.0,
        )
        assert finalize.status_code == 400
        combined = httpx.post(
            f"{server.base_url}/rest/2.0/import/synchronize/{UUID_A}/json-job",
            data=dict(IMPORT_MULTIPART_FIELDS),
            timeout=2.0,
        )
        assert combined.status_code == 400
        assert server.finalize_posts == 1
