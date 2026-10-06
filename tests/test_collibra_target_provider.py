"""Collibra target provider binding and projection tests (#97)."""

from __future__ import annotations

import pytest

from governance.config import Settings
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
from governance.identity import target_context_identity
from governance.integrations.collibra import map_to_desired_state, mock_mapping_config
from governance.orchestration.compat_v1 import collibra_binding_from_settings
from governance.orchestration.registry import build_provider_registry
from governance.orchestration.targets import invoke_remote_state_read, invoke_target_planning
from governance.plans.target_context import (
    build_collibra_target_context_projection,
    build_target_context_projection,
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


def _mock_settings() -> Settings:
    return Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="secret",
        postgres_source_name="governance-demo",
        inventory_output_path="inventory.json",
        collibra_mode="mock",
    )


def test_target_context_projection_v1_settings_vs_pure_helper() -> None:
    settings = _mock_settings()
    via_settings = build_target_context_projection(settings)

    sync_id = ""
    if settings.collibra_execution_mode == "sync_v2":
        from governance.integrations.collibra.synchronization import effective_synchronization_id

        sync_id = effective_synchronization_id(settings)
    via_pure = build_collibra_target_context_projection(
        mode=settings.collibra_mode,
        base_url=settings.collibra_base_url,
        execution_mode=settings.collibra_execution_mode,
        synchronization_id=sync_id,
        source_name=settings.postgres_source_name,
    )
    assert via_settings == via_pure
    assert target_context_identity(via_settings) == target_context_identity(via_pure)


def test_provider_runtime_build_target_context_matches_pure_helper() -> None:
    settings = _mock_settings()
    mapping = mock_mapping_config()
    registry = build_provider_registry(discover_external=False)
    binding = collibra_binding_from_settings(settings, mapping, registry)
    read_cap = binding.registration.bindings[0].factory(binding.runtime_context)
    runtime_projection = read_cap._runtime.build_target_context()  # noqa: SLF001
    assert runtime_projection == build_target_context_projection(settings)


def test_invoke_remote_state_and_planning_mock_mode() -> None:
    settings = _mock_settings()
    mapping = mock_mapping_config()
    registry = build_provider_registry(discover_external=False)
    binding = collibra_binding_from_settings(settings, mapping, registry)
    desired = map_to_desired_state(_tiny_model(), mapping)
    remote = invoke_remote_state_read(binding, desired)
    plan = invoke_target_planning(binding, desired, remote)
    assert plan.creates
    assert remote.assets == ()


def test_read_only_paths_do_not_invoke_authorized_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("authorized_mutation must not run on read-only paths")

    monkeypatch.setattr(
        "governance.providers.builtins.collibra.execute_collibra_plan",
        _boom,
    )
    settings = _mock_settings()
    mapping = mock_mapping_config()
    registry = build_provider_registry(discover_external=False)
    binding = collibra_binding_from_settings(settings, mapping, registry)
    desired = map_to_desired_state(_tiny_model(), mapping)
    remote = invoke_remote_state_read(binding, desired)
    invoke_target_planning(binding, desired, remote)
