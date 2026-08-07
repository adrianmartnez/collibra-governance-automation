"""Mock Collibra adapter contract tests (zero network)."""

from __future__ import annotations

import pytest

from governance.domain import (
    Column,
    Database,
    DataSource,
    GovernanceModel,
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_schema_id,
    make_table_id,
)
from governance.integrations.collibra import (
    CollibraAdapterError,
    CollibraAssetSpec,
    CollibraAttributeSpec,
    CollibraDesiredState,
    MockCollibraAdapter,
    map_to_desired_state,
    mock_mapping_config,
    mock_remote_id,
)


def _tiny_model() -> GovernanceModel:
    source = "governance-demo"
    database = "governance_demo"
    schema = "commerce"
    table = "customers"
    column = "email"
    table_id = make_table_id(source, database, schema, table)
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
                        schemas=(
                            Schema(
                                id=make_schema_id(source, database, schema),
                                name=schema,
                                database_id=make_database_id(source, database),
                                tables=(
                                    Table(
                                        id=table_id,
                                        name=table,
                                        schema_id=make_schema_id(source, database, schema),
                                        columns=(
                                            Column(
                                                id=make_column_id(
                                                    source,
                                                    database,
                                                    schema,
                                                    table,
                                                    column,
                                                ),
                                                name=column,
                                                data_type="varchar",
                                                ordinal_position=1,
                                                nullable=False,
                                                description="email desc",
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )


def test_mock_mode_deterministic_ids_and_inspection() -> None:
    config = mock_mapping_config()
    adapter = MockCollibraAdapter(config)
    desired = map_to_desired_state(_tiny_model(), config)
    assert adapter.mode == "mock"

    remote = adapter.read_remote_state(desired)
    assert remote.assets == ()
    remote_id = adapter.create_asset(desired.assets[0])
    assert remote_id == mock_remote_id(desired.assets[0].local_id)
    assert any(action["operation"] == "create_asset" for action in adapter.actions)

    remote_after = adapter.read_remote_state(desired)
    assert len(remote_after.assets) == 1
    assert remote_after.assets[0].local_id == desired.assets[0].local_id


def test_unmanaged_without_local_id_ignored_and_same_name_no_match() -> None:
    config = mock_mapping_config()
    adapter = MockCollibraAdapter(config)
    desired = map_to_desired_state(_tiny_model(), config)
    target = desired.assets[0]
    adapter.seed_unmanaged_asset(
        remote_id="tenant-unmanaged-1",
        name=target.name,
        display_name=target.display_name,
        custom_attributes={"tenant:custom": "keep-me"},
    )
    remote = adapter.read_remote_state(desired)
    assert remote.assets == ()
    assert remote.unmanaged_assets_ignored == 1


def test_unmanaged_attribute_survives_directed_update() -> None:
    config = mock_mapping_config()
    adapter = MockCollibraAdapter(config)
    desired = map_to_desired_state(_tiny_model(), config)
    asset = next(item for item in desired.assets if item.local_id.startswith("col:"))
    remote_id = adapter.create_asset(asset)
    adapter.seed_custom_attribute(remote_id, "tenant:custom", "preserve")

    new_attrs = tuple(
        CollibraAttributeSpec(attr.attribute_type_ref, "updated description")
        if attr.attribute_type_ref == config.attribute_type_refs["description"]
        else attr
        for attr in asset.attributes
    )
    updated = CollibraAssetSpec(
        local_id=asset.local_id,
        name=asset.name,
        display_name=asset.display_name,
        asset_type_ref=asset.asset_type_ref,
        domain_ref=asset.domain_ref,
        attributes=new_attrs,
    )
    adapter.clear_actions()
    adapter.update_asset(remote_id, updated)
    assert adapter.get_stored_attribute_value(remote_id, "tenant:custom") == "preserve"
    assert (
        adapter.get_stored_attribute_value(remote_id, config.attribute_type_refs["description"])
        == "updated description"
    )
    assert any(action["operation"] == "patch_attribute" for action in adapter.actions)
    assert not any("delete" in action["operation"] for action in adapter.actions)


def test_missing_managed_attribute_created_unchanged_noop() -> None:
    config = mock_mapping_config()
    adapter = MockCollibraAdapter(config)
    desired = map_to_desired_state(_tiny_model(), config)
    asset = next(item for item in desired.assets if item.local_id.startswith("col:"))
    stripped = CollibraAssetSpec(
        local_id=asset.local_id,
        name=asset.name,
        display_name=asset.display_name,
        asset_type_ref=asset.asset_type_ref,
        domain_ref=asset.domain_ref,
        attributes=tuple(
            attr
            for attr in asset.attributes
            if attr.attribute_type_ref != config.attribute_type_refs["description"]
        ),
    )
    remote_id = adapter.create_asset(stripped)
    adapter.clear_actions()
    adapter.update_asset(remote_id, asset)
    assert any(action["operation"] == "create_attribute" for action in adapter.actions)
    adapter.clear_actions()
    adapter.update_asset(
        remote_id,
        asset,
        patch_name=False,
        patch_display_name=False,
    )
    assert adapter.actions == [
        {
            "operation": "update_asset",
            "remote_id": remote_id,
            "local_id": asset.local_id,
            "patch_name": False,
            "patch_display_name": False,
        }
    ]


def test_unrelated_relation_ignored() -> None:
    config = mock_mapping_config()
    adapter = MockCollibraAdapter(config)
    desired = map_to_desired_state(_tiny_model(), config)
    for asset in desired.assets:
        adapter.create_asset(asset)
    adapter.seed_unmanaged_relationship(
        remote_id="tenant-rel-1",
        source_remote_id=mock_remote_id(desired.assets[0].local_id),
        target_remote_id=mock_remote_id(desired.assets[1].local_id),
        relation_type_ref="tenant:unrelated-relation-type",
    )
    remote = adapter.read_remote_state(desired)
    assert remote.unmanaged_relationships_ignored == 1
    assert remote.relationships == ()


def test_injected_failure() -> None:
    adapter = MockCollibraAdapter(mock_mapping_config())
    desired = map_to_desired_state(_tiny_model(), mock_mapping_config())
    adapter.fail_next("create_asset")
    with pytest.raises(CollibraAdapterError, match="injected mock failure") as exc_info:
        adapter.create_asset(desired.assets[0])
    assert exc_info.value.operation == "create_asset"


def test_empty_desired_remote_state_type() -> None:
    adapter = MockCollibraAdapter(mock_mapping_config())
    state = adapter.read_remote_state(CollibraDesiredState(assets=()))
    assert state.to_dict()["unmanaged_assets_ignored"] == 0
