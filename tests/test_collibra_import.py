"""Import API v2 compiler and json-job submit tests."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import pytest

from governance.config import Settings
from governance.domain import (
    Database,
    DataSource,
    GovernanceModel,
    make_database_id,
    make_datasource_id,
)
from governance.integrations.collibra import (
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraDesiredState,
    CollibraRelationshipSpec,
    CollibraRemoteState,
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
    map_to_desired_state,
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
        if request.method == "GET" and path.endswith("/jobs/job-123"):
            return httpx.Response(
                200, json={"id": "job-123", "state": "COMPLETED", "result": "SUCCESS"}
            )
        return httpx.Response(500, json={"error": "unexpected"})

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    result = execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert [urlparse(str(item.url)).path for item in requests] == [
        "/rest/2.0/import/json-job",
        "/rest/2.0/jobs/job-123",
    ]
    assert result.success is True
    assert result.applied_count == 1
    assert getattr(result, "completed", None) is not True


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
        if request.method == "GET" and path.endswith("/jobs/job-1"):
            return httpx.Response(
                200, json={"id": "job-1", "state": "COMPLETED", "result": "SUCCESS"}
            )
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
    assert applied.success is True
    assert applied.applied_count == 1
    assert [urlparse(str(item.url)).path for item in requests] == [
        "/rest/2.0/import/json-job",
        "/rest/2.0/jobs/job-1",
    ]
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


def test_unknown_execution_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="core_rest, import_v2, or sync_v2"):
        _settings(collibra_execution_mode="not_a_mode")


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
            assert request.url.params.get("excludeMeta") == "false"
            assert request.url.params.get("typeId") is None
            return httpx.Response(200, json=_empty_page())
        if request.method == "POST" and path.endswith("/import/json-job"):
            return httpx.Response(200, json={"id": "job-create"})
        if request.method == "GET" and path.endswith("/jobs/job-create"):
            return httpx.Response(
                200, json={"id": "job-create", "state": "COMPLETED", "result": "SUCCESS"}
            )
        return httpx.Response(500, json={"error": "unexpected"})

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    result = execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert result.success is True
    assert result.applied_count == 1
    assert [urlparse(str(item.url)).path for item in requests] == [
        "/rest/2.0/assets",
        "/rest/2.0/import/json-job",
        "/rest/2.0/jobs/job-create",
    ]
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


def _assert_exact_collision_query(request: httpx.Request, *, name: str, domain_ref: str) -> None:
    assert request.method == "GET"
    assert urlparse(str(request.url)).path == "/rest/2.0/assets"
    assert request.url.params.get("name") == name
    assert request.url.params.get("nameMatchMode") == "EXACT"
    assert request.url.params.get("domainId") == domain_ref
    assert request.url.params.get("excludeMeta") == "false"
    assert request.url.params.get("typeId") is None


def test_whitespace_create_identifier_is_looked_up_exactly() -> None:
    raw_name = " orders "
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
                    name=raw_name,
                    kind="table",
                    attrs=("local_id", "tbl:orders"),
                ),
            ),
        )
    )
    compiled = compile_import_document(plan, config)
    assert compiled.to_list()[0]["identifier"]["name"] == raw_name
    assert compiled.to_list()[0]["identifier"]["name"] != raw_name.strip()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            _assert_exact_collision_query(request, name=raw_name, domain_ref=config.domain_ref)
            return httpx.Response(200, json=_empty_page())
        if request.method == "POST" and path.endswith("/import/json-job"):
            return httpx.Response(200, json={"id": "job-ws"})
        return httpx.Response(500, json={"error": "unexpected"})

    adapter = LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )
    result = execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert result.submitted is True
    assert requests[0].url.params.get("name") == " orders "
    assert requests[0].url.params.get("name") != "orders"
    assert compiled.canonical_json() in requests[1].content


def test_whitespace_occupant_is_fail_closed_zero_post() -> None:
    raw_name = " orders "
    config = mock_mapping_config()
    plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.CREATE,
                object_kind=SyncObjectKind.ASSET,
                local_id="tbl:orders",
                reason="create",
                desired_asset=_asset(local_id="tbl:orders", name=raw_name, kind="table"),
            ),
        )
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = urlparse(str(request.url)).path
        if request.method == "GET" and path.endswith("/assets"):
            _assert_exact_collision_query(request, name=raw_name, domain_ref=config.domain_ref)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "unmanaged-ws",
                            "name": raw_name,
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
    assert all(request.method != "POST" for request in requests)


def test_whitespace_create_reordered_plan_is_deterministic() -> None:
    config = mock_mapping_config()
    spaced = _create_asset_action(local_id="tbl:orders", name=" orders ", kind="table")
    other = _create_asset_action(local_id="tbl:items", name="items", kind="table")
    left = compile_import_document(SyncPlan(actions=(spaced, other)), config)
    right = compile_import_document(SyncPlan(actions=(other, spaced)), config)
    assert left.canonical_json() == right.canonical_json()
    names = [command["identifier"]["name"] for command in left.to_list()]
    assert " orders " in names
    assert "orders" not in names


def _duplicate_create_plan() -> tuple[object, SyncAction, SyncAction]:
    config = mock_mapping_config()
    first = SyncAction(
        action_type=SyncActionType.CREATE,
        object_kind=SyncObjectKind.ASSET,
        local_id="tbl:orders-a",
        reason="create",
        desired_asset=_asset(local_id="tbl:orders-a", name="orders", kind="table"),
    )
    second = SyncAction(
        action_type=SyncActionType.CREATE,
        object_kind=SyncObjectKind.ASSET,
        local_id="tbl:orders-b",
        reason="create",
        desired_asset=_asset(local_id="tbl:orders-b", name="orders", kind="table"),
    )
    return config, first, second


def test_duplicate_create_natural_identifier_is_compile_error() -> None:
    config, first, second = _duplicate_create_plan()
    with pytest.raises(ImportCompileError, match="same name and domain"):
        compile_import_document(SyncPlan(actions=(first, second)), config)


def test_duplicate_create_natural_identifier_is_zero_network() -> None:
    config, first, second = _duplicate_create_plan()
    plan = SyncPlan(actions=(first, second))
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
    with pytest.raises(ImportCompileError, match="same name and domain"):
        execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert requests == []


def test_duplicate_create_natural_identifier_reordered_is_deterministic() -> None:
    config, first, second = _duplicate_create_plan()
    with pytest.raises(ImportCompileError, match="same name and domain"):
        compile_import_document(SyncPlan(actions=(second, first)), config)


def test_distinct_create_natural_identifiers_still_compile() -> None:
    config = mock_mapping_config()
    plan = SyncPlan(
        actions=(
            _create_asset_action(local_id="tbl:orders", name="orders", kind="table"),
            _create_asset_action(local_id="tbl:items", name="items", kind="table"),
        )
    )
    document = compile_import_document(plan, config)
    names = [command["identifier"]["name"] for command in document.to_list()]
    assert set(names) == {"orders", "items"}


def test_mapper_same_name_distinct_local_ids_rejected_by_import() -> None:
    config = mock_mapping_config()
    model = GovernanceModel(
        data_sources=(
            DataSource(
                id=make_datasource_id("src-a"),
                name="src-a",
                system_type="postgresql",
                databases=(
                    Database(
                        id=make_database_id("src-a", "shared"),
                        name="shared",
                        datasource_id=make_datasource_id("src-a"),
                    ),
                ),
            ),
            DataSource(
                id=make_datasource_id("src-b"),
                name="src-b",
                system_type="postgresql",
                databases=(
                    Database(
                        id=make_database_id("src-b", "shared"),
                        name="shared",
                        datasource_id=make_datasource_id("src-b"),
                    ),
                ),
            ),
        )
    )
    desired = map_to_desired_state(model, config)
    shared = [asset for asset in desired.assets if asset.name == "shared"]
    assert len(shared) == 2
    assert shared[0].local_id != shared[1].local_id
    assert shared[0].domain_ref == shared[1].domain_ref == config.domain_ref
    plan = build_sync_plan(desired, CollibraRemoteState())
    with pytest.raises(ImportCompileError, match="same name and domain"):
        compile_import_document(plan, config)


def _mismatched_create(
    *,
    action_local_id: str | None,
    desired_local_id: str,
    name: str = "orders",
) -> SyncAction:
    return SyncAction(
        action_type=SyncActionType.CREATE,
        object_kind=SyncObjectKind.ASSET,
        local_id=action_local_id,
        reason="create",
        desired_asset=_asset(local_id=desired_local_id, name=name, kind="table"),
    )


def _recording_live_adapter(config: object, requests: list[httpx.Request]) -> LiveCollibraAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    return LiveCollibraAdapter.from_settings(
        _settings(),
        config,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
    )


def test_create_action_desired_local_id_mismatch_is_compile_error() -> None:
    config = mock_mapping_config()
    plan = SyncPlan(actions=(_mismatched_create(action_local_id="A", desired_local_id="B"),))
    with pytest.raises(ImportCompileError, match="local_id must equal"):
        compile_import_document(plan, config)


def test_create_action_desired_local_id_mismatch_is_zero_network() -> None:
    config = mock_mapping_config()
    plan = SyncPlan(actions=(_mismatched_create(action_local_id="A", desired_local_id="B"),))
    requests: list[httpx.Request] = []
    adapter = _recording_live_adapter(config, requests)
    with pytest.raises(ImportCompileError, match="local_id must equal"):
        execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert requests == []


def test_create_missing_action_local_id_is_compile_error_zero_network() -> None:
    config = mock_mapping_config()
    plan = SyncPlan(actions=(_mismatched_create(action_local_id=None, desired_local_id="A"),))
    requests: list[httpx.Request] = []
    adapter = _recording_live_adapter(config, requests)
    with pytest.raises(ImportCompileError, match="missing local identity"):
        compile_import_document(plan, config)
    with pytest.raises(ImportCompileError, match="missing local identity"):
        execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert requests == []


def test_duplicate_guard_cannot_be_bypassed_by_divergent_desired_local_ids() -> None:
    config = mock_mapping_config()
    plan = SyncPlan(
        actions=(
            _mismatched_create(action_local_id="same", desired_local_id="A"),
            _mismatched_create(action_local_id="same", desired_local_id="B"),
        )
    )
    requests: list[httpx.Request] = []
    adapter = _recording_live_adapter(config, requests)
    with pytest.raises(ImportCompileError, match="local_id must equal"):
        compile_import_document(plan, config)
    with pytest.raises(ImportCompileError, match="local_id must equal"):
        execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert requests == []


def test_update_action_desired_local_id_mismatch_is_compile_error_zero_network() -> None:
    config = mock_mapping_config()
    plan = SyncPlan(
        actions=(
            SyncAction(
                action_type=SyncActionType.UPDATE,
                object_kind=SyncObjectKind.ASSET,
                local_id="A",
                remote_id="asset-1",
                reason="attr",
                desired_asset=_asset(local_id="B", name="orders", kind="table"),
                changed_fields=("name",),
            ),
        )
    )
    requests: list[httpx.Request] = []
    adapter = _recording_live_adapter(config, requests)
    with pytest.raises(ImportCompileError, match="local_id must equal"):
        compile_import_document(plan, config)
    with pytest.raises(ImportCompileError, match="local_id must equal"):
        execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert requests == []


def test_build_sync_plan_create_still_compiles() -> None:
    config = mock_mapping_config()
    model = GovernanceModel(
        data_sources=(
            DataSource(
                id=make_datasource_id("src"),
                name="src",
                system_type="postgresql",
                databases=(
                    Database(
                        id=make_database_id("src", "demo"),
                        name="demo",
                        datasource_id=make_datasource_id("src"),
                    ),
                ),
            ),
        )
    )
    desired = map_to_desired_state(model, config)
    plan = build_sync_plan(desired, CollibraRemoteState())
    for action in plan.actions:
        if action.object_kind is SyncObjectKind.ASSET and action.action_type in {
            SyncActionType.CREATE,
            SyncActionType.UPDATE,
        }:
            assert action.desired_asset is not None
            assert action.local_id == action.desired_asset.local_id
    first = compile_import_document(plan, config)
    restored = SyncPlan.from_dict(plan.to_dict())
    second = compile_import_document(restored, config)
    assert first.canonical_json() == second.canonical_json()
    assert first.to_list()


def test_saved_plan_from_dict_mismatch_rejected_before_network() -> None:
    config = mock_mapping_config()
    mismatched = _mismatched_create(action_local_id="A", desired_local_id="B")
    restored = SyncAction.from_dict(mismatched.to_dict())
    assert restored.local_id == "A"
    assert restored.desired_asset is not None
    assert restored.desired_asset.local_id == "B"
    plan = SyncPlan.from_dict(SyncPlan(actions=(restored,)).to_dict())
    requests: list[httpx.Request] = []
    adapter = _recording_live_adapter(config, requests)
    with pytest.raises(ImportCompileError, match="local_id must equal"):
        compile_import_document(plan, config)
    with pytest.raises(ImportCompileError, match="local_id must equal"):
        execute_collibra_plan(adapter, plan, config, apply=True, execution_mode="import_v2")
    assert requests == []
