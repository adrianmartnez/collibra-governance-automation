"""Import API v2 compiler and json-job submit tests."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import pytest

from governance.config import Settings
from governance.integrations.collibra import (
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraDesiredState,
    CollibraRelationshipSpec,
    ImportCollisionError,
    ImportCompileError,
    ImportExecutionResult,
    LiveCollibraAdapter,
    MockCollibraAdapter,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
    build_sync_plan,
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


def _asset(
    *,
    local_id: str = "tbl:demo",
    name: str = "demo",
    kind: str = "table",
    attrs: tuple[str, str] | None = None,
):
    config = mock_mapping_config()
    attributes = ()
    if attrs is not None:
        attributes = (CollibraAttributeSpec(config.attribute_type_refs[attrs[0]], attrs[1]),)
    return CollibraAssetSpec(
        local_id=local_id,
        name=name,
        display_name=name,
        asset_type_ref=config.asset_type_refs[kind],
        domain_ref=config.domain_ref,
        attributes=attributes,
    )


def _create_asset_action(
    *,
    local_id: str,
    name: str,
    kind: str,
) -> SyncAction:
    return SyncAction(
        action_type=SyncActionType.CREATE,
        object_kind=SyncObjectKind.ASSET,
        local_id=local_id,
        reason="create",
        desired_asset=_asset(local_id=local_id, name=name, kind=kind),
    )


def _schema_table_rel(rel_type: str) -> SyncAction:
    return SyncAction(
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


def test_both_create_relation_attaches_to_later_command() -> None:
    config = mock_mapping_config()
    rel_type = config.relation_type_refs["schema_table"]
    schema = _create_asset_action(local_id="sch:demo", name="sch", kind="schema")
    table = _create_asset_action(local_id="tbl:demo", name="tbl", kind="table")
    rel = _schema_table_rel(rel_type)
    document = compile_import_document(SyncPlan(actions=(schema, table, rel)), config)
    commands = document.to_list()
    assert len(commands) == 2
    assert commands[0]["identifier"]["name"] == "sch"
    assert "relations" not in commands[0]
    assert commands[1]["identifier"]["name"] == "tbl"
    assert commands[1]["relations"][f"{rel_type}:SOURCE"] == [
        {"name": "sch", "domain": {"id": config.domain_ref}}
    ]
    assert f"{rel_type}:TARGET" not in commands[0].get("relations", {})
    reordered = compile_import_document(SyncPlan(actions=(table, rel, schema)), config)
    assert reordered.canonical_json() == document.canonical_json()


def test_source_create_target_existing_relation_on_source() -> None:
    config = mock_mapping_config()
    rel_type = config.relation_type_refs["schema_table"]
    plan = SyncPlan(
        actions=(
            _create_asset_action(local_id="sch:demo", name="sch", kind="schema"),
            SyncAction(
                action_type=SyncActionType.UNCHANGED,
                object_kind=SyncObjectKind.ASSET,
                local_id="tbl:demo",
                remote_id="table-1",
                reason="exists",
            ),
            _schema_table_rel(rel_type),
        )
    )
    command = compile_import_document(plan, config).to_list()[0]
    assert command["identifier"]["name"] == "sch"
    assert command["relations"][f"{rel_type}:TARGET"] == [{"id": "table-1"}]


def test_source_existing_target_create_relation_on_target() -> None:
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
            _create_asset_action(local_id="tbl:demo", name="tbl", kind="table"),
            _schema_table_rel(rel_type),
        )
    )
    command = compile_import_document(plan, config).to_list()[0]
    assert command["identifier"]["name"] == "tbl"
    assert command["relations"][f"{rel_type}:SOURCE"] == [{"id": "schema-1"}]


def test_import_submit_preserves_job_id_without_claiming_completion() -> None:
    config = mock_mapping_config()
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
            return httpx.Response(200, json={"id": "job-123"})
        return httpx.Response(500, json={"error": "unexpected"})

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    result = execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert isinstance(result, ImportExecutionResult)
    assert len(requests) == 1
    assert urlparse(str(requests[0].url)).path == "/rest/2.0/import/json-job"
    assert result.job_id == "job-123"
    assert result.submission is not None
    assert result.submission.job_id == "job-123"
    assert result.submitted is True
    assert result.dry_run is False
    assert result.applied_count == 0
    assert getattr(result, "completed", None) is not True
    assert requests[0].method == "POST"


def test_import_dry_run_returns_inspectable_document() -> None:
    config = mock_mapping_config()
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
        return httpx.Response(500)

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    preview = execute_collibra_plan(adapter, plan, config, apply=False, execution_mode="import_v2")
    compiled = compile_import_document(plan, config)
    assert isinstance(preview, ImportExecutionResult)
    assert preview.document is not None
    assert preview.document.canonical_json() == compiled.canonical_json()
    assert preview.document.to_list() == compiled.to_list()
    assert requests == []
    assert preview.submitted is False
    assert preview.submission is None
    assert preview.job_id is None
    assert preview.applied_count == 0


def test_import_preview_matches_submitted_multipart_bytes() -> None:
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
    preview = execute_collibra_plan(adapter, plan, config, apply=False, execution_mode="import_v2")
    assert preview.submitted is False
    assert requests == []
    applied = execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert applied.submitted is True
    assert applied.applied_count == 0
    assert len(requests) == 1
    assert preview.document.canonical_json() in requests[0].content
    assert "secret-token-value-xyz" not in requests[0].content.decode("utf-8", errors="replace")


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


def _empty_page() -> dict[str, object]:
    return {"results": [], "total": 0}


def _create_plan() -> tuple[object, SyncPlan]:
    config = mock_mapping_config()
    plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id="tbl:orders",
                reason="create",
                desired_asset=_asset(
                    local_id="tbl:orders",
                    name="orders",
                    kind="table",
                    attrs=("local_id", "tbl:orders"),
                ),
            ),
        )
    )
    return config, plan


def test_create_absent_natural_identifier_allows_submit() -> None:
    config, plan = _create_plan()
    compiled = compile_import_document(plan, config)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            assert request.url.params.get("name") == "orders"
            assert request.url.params.get("nameMatchMode") == "EXACT"
            assert request.url.params.get("domainId") == config.domain_ref
            assert request.url.params.get("typeId") is None
            return httpx.Response(200, json=_empty_page())
        if request.method == "POST" and path.endswith("/import/json-job"):
            return httpx.Response(200, json={"id": "job-create"})
        return httpx.Response(500, json={"error": "unexpected"})

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    result = execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert isinstance(result, ImportExecutionResult)
    assert result.submitted is True
    assert result.job_id == "job-create"
    assert result.applied_count == 0
    methods = [request.method for request in requests]
    assert methods == ["GET", "POST"]
    assert urlparse(str(requests[0].url)).path == "/rest/2.0/assets"
    assert urlparse(str(requests[1].url)).path == "/rest/2.0/import/json-job"
    assert compiled.to_list()[0]["identifier"] == {
        "name": "orders",
        "domain": {"id": config.domain_ref},
    }


def test_unmanaged_name_domain_collision_is_fail_closed_zero_post() -> None:
    config, plan = _create_plan()
    requests: list[httpx.Request] = []

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
                            "name": "orders",
                            "domain": {"id": config.domain_ref},
                        }
                    ],
                    "total": 1,
                },
            )
        return httpx.Response(500, json={"error": "unexpected"})

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    with pytest.raises(ImportCollisionError, match="collides"):
        execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert requests
    assert all(request.method != "POST" for request in requests)
    assert urlparse(str(requests[0].url)).path == "/rest/2.0/assets"


def test_managed_existing_asset_is_update_or_unchanged_not_create() -> None:
    config = mock_mapping_config()
    adapter = MockCollibraAdapter(config)
    asset = _asset(
        local_id="tbl:orders",
        name="orders",
        kind="table",
        attrs=("local_id", "tbl:orders"),
    )
    adapter.create_asset(asset)
    desired = CollibraDesiredState(assets=(asset,))
    remote = adapter.read_remote_state(desired)
    plan = build_sync_plan(desired, remote)
    types = {action.action_type for action in plan.actions if action.local_id == "tbl:orders"}
    assert SyncActionType.CREATE not in types
    assert types <= {SyncActionType.UPDATE, SyncActionType.UNCHANGED}


def test_unmanaged_same_name_still_plans_create() -> None:
    config = mock_mapping_config()
    adapter = MockCollibraAdapter(config)
    adapter.seed_unmanaged_asset(remote_id="unmanaged-1", name="orders")
    desired = CollibraDesiredState(
        assets=(
            _asset(
                local_id="tbl:orders",
                name="orders",
                kind="table",
                attrs=("local_id", "tbl:orders"),
            ),
        )
    )
    remote = adapter.read_remote_state(desired)
    plan = build_sync_plan(desired, remote)
    action = next(item for item in plan.actions if item.local_id == "tbl:orders")
    assert action.action_type is SyncActionType.CREATE
    assert remote.unmanaged_assets_ignored >= 1


def test_collision_check_http_error_is_fail_closed_zero_post() -> None:
    config, plan = _create_plan()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, json={"error": "unavailable"})

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    with pytest.raises(ImportCollisionError, match="collision check failed"):
        execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert all(request.method != "POST" for request in requests)


def test_collision_check_malformed_response_is_fail_closed_zero_post() -> None:
    config, plan = _create_plan()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            return httpx.Response(200, json={"total": 1})
        return httpx.Response(500)

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    with pytest.raises(ImportCollisionError, match="collision check failed"):
        execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert all(request.method != "POST" for request in requests)


def test_collision_check_non_list_matches_is_fail_closed_zero_post() -> None:
    config, plan = _create_plan()
    posts: list[object] = []

    class AmbiguousAdapter:
        mode = "live"

        def lookup_assets_by_natural_identifier(self, *, name: str, domain_ref: str):
            del name, domain_ref
            return {"results": []}

        def submit_json_import(self, document: object) -> None:
            posts.append(document)
            raise AssertionError("POST must not run")

    with pytest.raises(ImportCollisionError, match="ambiguous"):
        execute_collibra_plan(
            AmbiguousAdapter(),
            plan,
            config,
            apply=True,
            execution_mode="import_v2",
        )
    assert posts == []


def test_create_without_lookup_capability_is_fail_closed() -> None:
    config, plan = _create_plan()
    posts: list[object] = []

    class NoLookupAdapter:
        mode = "live"

        def submit_json_import(self, document: object) -> None:
            posts.append(document)
            raise AssertionError("POST must not run")

    with pytest.raises(ImportCollisionError, match="unavailable"):
        execute_collibra_plan(
            NoLookupAdapter(),
            plan,
            config,
            apply=True,
            execution_mode="import_v2",
        )
    assert posts == []


def test_create_dry_run_is_offline_and_inspectable() -> None:
    config, plan = _create_plan()
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
    preview = execute_collibra_plan(adapter, plan, config, apply=False, execution_mode="import_v2")
    compiled = compile_import_document(plan, config)
    assert isinstance(preview, ImportExecutionResult)
    assert requests == []
    assert preview.submitted is False
    assert preview.job_id is None
    assert preview.document.canonical_json() == compiled.canonical_json()
    assert preview.document.to_list()[0]["identifier"]["name"] == "orders"
