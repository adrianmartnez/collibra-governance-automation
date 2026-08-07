"""Unit tests for Collibra sync plan and execution safety."""

from __future__ import annotations

from governance.domain import (
    Column,
    Database,
    DataSource,
    ForeignKey,
    GovernanceModel,
    Ownership,
    PrimaryKey,
    Relationship,
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_foreign_key_id,
    make_primary_key_id,
    make_relationship_id,
    make_schema_id,
    make_table_id,
)
from governance.integrations.collibra import (
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraDesiredState,
    MockCollibraAdapter,
    SyncActionType,
    build_sync_plan,
    execute_sync_plan,
    map_to_desired_state,
    mock_mapping_config,
)


def _model() -> GovernanceModel:
    source = "governance-demo"
    database = "governance_demo"
    schema = "commerce"
    customers_id = make_table_id(source, database, schema, "customers")
    orders_id = make_table_id(source, database, schema, "orders")
    customers_col = make_column_id(source, database, schema, "customers", "customer_id")
    orders_col = make_column_id(source, database, schema, "orders", "order_id")
    orders_fk_col = make_column_id(source, database, schema, "orders", "customer_id")
    fk_id = make_foreign_key_id(orders_id, "orders_customer_fkey")
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
                                        id=customers_id,
                                        name="customers",
                                        schema_id=make_schema_id(source, database, schema),
                                        description="customers table",
                                        ownership=Ownership(owner_name="governance_owner"),
                                        columns=(
                                            Column(
                                                id=customers_col,
                                                name="customer_id",
                                                data_type="uuid",
                                                ordinal_position=1,
                                                nullable=False,
                                                description="id",
                                            ),
                                        ),
                                        primary_key=PrimaryKey(
                                            id=make_primary_key_id(customers_id, "customers_pkey"),
                                            name="customers_pkey",
                                            table_id=customers_id,
                                            column_ids=(customers_col,),
                                        ),
                                    ),
                                    Table(
                                        id=orders_id,
                                        name="orders",
                                        schema_id=make_schema_id(source, database, schema),
                                        ownership=Ownership(owner_name="governance_owner"),
                                        columns=(
                                            Column(
                                                id=orders_col,
                                                name="order_id",
                                                data_type="bigint",
                                                ordinal_position=1,
                                                nullable=False,
                                            ),
                                            Column(
                                                id=orders_fk_col,
                                                name="customer_id",
                                                data_type="uuid",
                                                ordinal_position=2,
                                                nullable=False,
                                            ),
                                        ),
                                        primary_key=PrimaryKey(
                                            id=make_primary_key_id(orders_id, "orders_pkey"),
                                            name="orders_pkey",
                                            table_id=orders_id,
                                            column_ids=(orders_col,),
                                        ),
                                        foreign_keys=(
                                            ForeignKey(
                                                id=fk_id,
                                                name="orders_customer_fkey",
                                                table_id=orders_id,
                                                column_ids=(orders_fk_col,),
                                                referenced_table_id=customers_id,
                                                referenced_column_ids=(customers_col,),
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
        relationships=(
            Relationship(
                id=make_relationship_id(fk_id),
                name="orders_customer_fkey",
                from_table_id=orders_id,
                to_table_id=customers_id,
                foreign_key_id=fk_id,
            ),
        ),
    )


def test_empty_remote_creates_then_idempotent_second_sync() -> None:
    config = mock_mapping_config()
    desired = map_to_desired_state(_model(), config)
    adapter = MockCollibraAdapter(config)

    remote = adapter.read_remote_state(desired)
    plan = build_sync_plan(desired, remote)
    assert plan.creates
    assert not any(action.action_type.value == "delete" for action in plan.actions)
    assert "DELETE" not in SyncActionType.__members__

    dry = execute_sync_plan(adapter, plan, apply=False)
    assert dry.success and dry.dry_run and dry.applied_count == 0
    write_ops = [
        action["operation"]
        for action in adapter.actions
        if action["operation"] in {"create_asset", "update_asset", "create_relationship"}
    ]
    assert write_ops == []

    applied = execute_sync_plan(adapter, plan, apply=True)
    assert applied.success and not applied.dry_run
    assert applied.applied_count == len(plan.creates)

    remote2 = adapter.read_remote_state(desired)
    plan2 = build_sync_plan(desired, remote2)
    assert plan2.creates == ()
    assert plan2.updates == ()
    assert plan2.unchanged
    noop = execute_sync_plan(adapter, plan2, apply=True)
    assert noop.success
    assert noop.applied_count == 0


def test_dependency_order_assets_before_relationships() -> None:
    config = mock_mapping_config()
    desired = map_to_desired_state(_model(), config)
    adapter = MockCollibraAdapter(config)
    plan = build_sync_plan(desired, adapter.read_remote_state(desired))
    adapter.clear_actions()
    execute_sync_plan(adapter, plan, apply=True)
    operations = [action["operation"] for action in adapter.actions]
    first_rel = operations.index("create_relationship")
    assert all(op != "create_relationship" for op in operations[:first_rel])
    created_ids = [
        action["local_id"] for action in adapter.actions if action["operation"] == "create_asset"
    ]
    prefixes = [local_id.split(":", 1)[0] for local_id in created_ids]
    assert prefixes == sorted(prefixes, key=["db", "sch", "tbl", "col"].index)


def test_metadata_only_update_and_remote_only_managed() -> None:
    config = mock_mapping_config()
    desired = map_to_desired_state(_model(), config)
    adapter = MockCollibraAdapter(config)
    plan = build_sync_plan(desired, adapter.read_remote_state(desired))
    execute_sync_plan(adapter, plan, apply=True)

    target = next(asset for asset in desired.assets if asset.local_id.endswith("/customers"))
    changed_attrs = tuple(
        CollibraAttributeSpec(attr.attribute_type_ref, "customers table updated")
        if attr.attribute_type_ref == config.attribute_type_refs["description"]
        else attr
        for attr in target.attributes
    )
    changed_asset = CollibraAssetSpec(
        local_id=target.local_id,
        name=target.name,
        display_name=target.display_name,
        asset_type_ref=target.asset_type_ref,
        domain_ref=target.domain_ref,
        attributes=changed_attrs,
    )
    new_assets = tuple(
        changed_asset if asset.local_id == target.local_id else asset for asset in desired.assets
    )
    changed_desired = CollibraDesiredState(
        assets=new_assets,
        relationships=desired.relationships,
    )
    plan2 = build_sync_plan(changed_desired, adapter.read_remote_state(changed_desired))
    assert len(plan2.updates) == 1
    assert plan2.updates[0].local_id == target.local_id
    assert plan2.updates[0].changed_fields == ("managed_attributes",)
    assert plan2.creates == ()
    adapter.clear_actions()
    execute_sync_plan(adapter, plan2, apply=True)
    assert not any(
        action["operation"] in {"patch_asset_name", "patch_asset_display_name"}
        for action in adapter.actions
    )
    assert any(action["operation"] == "patch_attribute" for action in adapter.actions)

    # Managed remote-only: remove one desired asset identity by building remote snapshot
    remote = adapter.read_remote_state(desired)
    smaller = CollibraDesiredState(
        assets=tuple(asset for asset in desired.assets if not asset.local_id.startswith("col:")),
        relationships=(),
    )
    plan3 = build_sync_plan(smaller, remote)
    assert plan3.remote_only
    assert all(action.local_id for action in plan3.remote_only)
    assert not any(action.action_type.value == "delete" for action in plan3.actions)


def test_unmanaged_remote_not_remote_only() -> None:
    config = mock_mapping_config()
    desired = map_to_desired_state(_model(), config)
    adapter = MockCollibraAdapter(config)
    adapter.seed_unmanaged_asset(
        remote_id="tenant-1",
        name=desired.assets[0].name,
    )
    remote = adapter.read_remote_state(desired)
    assert remote.unmanaged_assets_ignored == 1
    plan = build_sync_plan(desired, remote)
    assert plan.remote_only == ()


def test_fail_fast_on_injected_write_failure() -> None:
    config = mock_mapping_config()
    desired = map_to_desired_state(_model(), config)
    adapter = MockCollibraAdapter(config)
    plan = build_sync_plan(desired, adapter.read_remote_state(desired))
    adapter.fail_next("create_asset")
    result = execute_sync_plan(adapter, plan, apply=True)
    assert not result.success
    assert result.failed_action is not None
    assert result.error is not None
