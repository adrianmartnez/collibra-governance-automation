"""Import API v2 compiler and json-job submit tests."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import pytest

from governance.config import Settings
from governance.integrations.collibra import (
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraRelationshipSpec,
    ImportCompileError,
    LiveCollibraAdapter,
    MockCollibraAdapter,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
    compile_import_document,
    execute_collibra_plan,
    mock_mapping_config,
)
from governance.integrations.collibra.import_api import IMPORT_MULTIPART_FIELDS
from governance.plans.target_context import build_target_context_projection


def _settings(**overrides: object) -> Settings:
    base = {
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


def _asset(*, local_id: str = "tbl:demo", name: str = "demo", attrs: tuple[str, str] | None = None):
    config = mock_mapping_config()
    attributes = ()
    if attrs is not None:
        attributes = (CollibraAttributeSpec(config.attribute_type_refs[attrs[0]], attrs[1]),)
    return CollibraAssetSpec(
        local_id=local_id,
        name=name,
        display_name=name,
        asset_type_ref=config.asset_type_refs["table"],
        domain_ref=config.domain_ref,
        attributes=attributes,
    )


def test_create_emits_only_managed_attribute_types() -> None:
    config = mock_mapping_config()
    local_id_type = config.attribute_type_refs["local_id"]
    plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id="tbl:demo",
                reason="create",
                desired_asset=_asset(attrs=("local_id", "tbl:demo")),
            ),
        )
    )
    document = compile_import_document(plan, config)
    command = document.to_list()[0]
    assert command["resourceType"] == "Asset"
    assert command["identifier"]["name"] == "demo"
    assert command["identifier"]["domain"]["id"] == config.domain_ref
    assert list(command["attributes"]) == [local_id_type]
    assert command["attributes"][local_id_type] == [{"value": "tbl:demo"}]


def test_update_managed_attribute_emits_new_value_only() -> None:
    config = mock_mapping_config()
    desc = config.attribute_type_refs["description"]
    plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.UPDATE,
                object_kind=SyncObjectKind.ASSET,
                local_id="tbl:demo",
                remote_id="asset-1",
                reason="attr",
                desired_asset=_asset(attrs=("description", "new")),
                changed_fields=("managed_attributes",),
            ),
        )
    )
    command = compile_import_document(plan, config).to_list()[0]
    assert command["identifier"] == {"id": "asset-1"}
    assert command["attributes"][desc] == [{"value": "new"}]
    assert "old" not in str(command)


def test_unmanaged_attribute_type_rejected_before_http() -> None:
    config = mock_mapping_config()
    asset = CollibraAssetSpec(
        local_id="tbl:demo",
        name="demo",
        display_name="demo",
        asset_type_ref=config.asset_type_refs["table"],
        domain_ref=config.domain_ref,
        attributes=(CollibraAttributeSpec("unmanaged-type-id", "x"),),
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
    with pytest.raises(ImportCompileError, match="unmanaged attribute"):
        compile_import_document(plan, config)


def test_relation_attached_with_add_or_ignore_semantics() -> None:
    config = mock_mapping_config()
    rel_type = config.relation_type_refs["schema_table"]
    plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.UNCHANGED,
                object_kind=SyncObjectKind.ASSET,
                local_id="sch:demo",
                remote_id="schema-1",
                reason="exists",
            ),
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.RELATIONSHIP,
                local_id="rel:1",
                reason="rel",
                desired_relationship=CollibraRelationshipSpec(
                    local_key="rel:1",
                    source_local_id="sch:demo",
                    target_local_id="tbl:demo",
                    relation_type_ref=rel_type,
                ),
            ),
            SyncAction(
                action_type=SyncActionType.UNCHANGED,
                object_kind=SyncObjectKind.ASSET,
                local_id="tbl:demo",
                remote_id="table-1",
                reason="exists",
            ),
        )
    )
    command = compile_import_document(plan, config).to_list()[0]
    assert command["identifier"] == {"id": "schema-1"}
    assert command["relations"][f"{rel_type}:TARGET"] == [{"id": "table-1"}]
    assert "attributes" not in command


def test_reordered_input_compiles_equivalently() -> None:
    config = mock_mapping_config()
    a = SyncAction(
        action_type=SyncActionType.CREATE,
        object_kind=SyncObjectKind.ASSET,
        local_id="tbl:a",
        reason="a",
        desired_asset=_asset(local_id="tbl:a", name="a"),
    )
    b = SyncAction(
        action_type=SyncActionType.CREATE,
        object_kind=SyncObjectKind.ASSET,
        local_id="tbl:b",
        reason="b",
        desired_asset=_asset(local_id="tbl:b", name="b"),
    )
    left = compile_import_document(SyncPlan(actions=(a, b)), config).canonical_json()
    right = compile_import_document(SyncPlan(actions=(b, a)), config).canonical_json()
    assert left == right


def test_import_submit_multipart_and_dry_run_zero_posts() -> None:
    config = mock_mapping_config()
    desc = config.attribute_type_refs["description"]
    plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.UPDATE,
                object_kind=SyncObjectKind.ASSET,
                local_id="tbl:demo",
                remote_id="asset-1",
                reason="attr",
                desired_asset=_asset(attrs=("description", "new-description")),
                changed_fields=("managed_attributes",),
            ),
        )
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "POST" and path.endswith("/import/json-job"):
            body = request.content.decode("utf-8", errors="replace")
            for key, value in IMPORT_MULTIPART_FIELDS.items():
                assert key in body
                assert value in body
            assert "file" in body
            assert "new-description" in body
            assert "old-description" not in body
            assert desc in body
            assert "secret-token-value-xyz" not in body
            assert "/import/synchronize" not in path
            return httpx.Response(200, json={"id": "job-1"})
        return httpx.Response(500, json={"error": "unexpected"})

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    dry = execute_collibra_plan(adapter, plan, config, apply=False, execution_mode="import_v2")
    assert dry.dry_run is True
    assert dry.applied_count == 0
    assert requests == []

    applied = execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert applied.success is True
    assert applied.applied_count == 1
    assert len(requests) == 1
    assert urlparse(str(requests[0].url)).path == "/rest/2.0/import/json-job"


def test_unchanged_managed_attributes_are_idempotent() -> None:
    config = mock_mapping_config()
    plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.UNCHANGED,
                object_kind=SyncObjectKind.ASSET,
                local_id="tbl:demo",
                remote_id="asset-1",
                reason="same",
                desired_asset=_asset(attrs=("description", "same")),
            ),
        )
    )
    assert compile_import_document(plan, config).commands == ()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    result = execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert result.success is True
    assert result.applied_count == 0
    assert requests == []


def test_core_rest_does_not_call_import() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    live = LiveCollibraAdapter.from_settings(
        _settings(),
        mock_mapping_config(),
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id="tbl:demo",
                reason="create",
                desired_asset=_asset(attrs=("local_id", "tbl:demo")),
            ),
        )
    )
    mock = MockCollibraAdapter(mock_mapping_config())
    result = execute_collibra_plan(
        mock, plan, mock_mapping_config(), apply=True, execution_mode="import_v2"
    )
    assert result.success is True
    assert requests == []
    core = execute_collibra_plan(
        live, SyncPlan(actions=()), mock_mapping_config(), apply=True, execution_mode="core_rest"
    )
    assert core.success is True
    assert requests == []


def test_sync_v2_is_not_a_valid_execution_mode() -> None:
    with pytest.raises(ValueError, match="core_rest or import_v2"):
        _settings(collibra_execution_mode="sync_v2")


def test_import_v2_binds_execution_in_target_context_identity() -> None:
    core = build_target_context_projection(_settings())
    imported = build_target_context_projection(_settings(collibra_execution_mode="import_v2"))
    assert "execution" not in core
    assert imported["execution"] == "import_v2"
    assert core["provider"] == imported["provider"]
    assert core["endpoint"] == imported["endpoint"]
