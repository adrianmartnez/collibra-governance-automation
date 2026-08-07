"""Collibra mock lifecycle against the local PostgreSQL demo (no Collibra network)."""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from governance.config import load_settings
from governance.exporters import MetadataInventory
from governance.integrations.collibra import (
    MockCollibraAdapter,
    SyncActionType,
    build_sync_plan,
    execute_sync_plan,
    map_to_desired_state,
    mock_mapping_config,
)
from governance.scanner import PostgresMetadataScanner

pytestmark = pytest.mark.collibra_integration


def _demo_settings():
    return load_settings(dotenv_path=None, environ={})


def _connect_autocommit(settings):
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=5,
        row_factory=dict_row,
        autocommit=True,
    )


def _read_column_comment(connection, schema: str, table: str, column: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT col_description(c.oid, a.attnum) AS description
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
            WHERE n.nspname = %s
              AND c.relname = %s
              AND a.attname = %s
              AND a.attnum > 0
              AND NOT a.attisdropped
            """,
            (schema, table, column),
        )
        row = cursor.fetchone()
        if row is None:
            raise AssertionError(f"column not found: {schema}.{table}.{column}")
        return row["description"]


def _set_column_comment(
    connection, schema: str, table: str, column: str, comment: str | None
) -> None:
    if comment is None:
        statement = sql.SQL("COMMENT ON COLUMN {}.{} IS NULL").format(
            sql.Identifier(schema, table),
            sql.Identifier(column),
        )
    else:
        statement = sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(
            sql.Identifier(schema, table),
            sql.Identifier(column),
            sql.Literal(comment),
        )
    with connection.cursor() as cursor:
        cursor.execute(statement)


def test_collibra_mock_lifecycle_scan_map_plan_apply_idempotent_and_comment_diff() -> None:
    settings = _demo_settings()
    scanner = PostgresMetadataScanner(settings)
    config = mock_mapping_config()
    adapter = MockCollibraAdapter(config)

    model = scanner.scan()
    inventory = MetadataInventory.from_model(model)
    desired = map_to_desired_state(inventory, config)

    assert any(asset.local_id.startswith("db:") for asset in desired.assets)
    assert any(asset.local_id.startswith("sch:") for asset in desired.assets)
    assert any(asset.local_id.startswith("tbl:") for asset in desired.assets)
    assert any(asset.local_id.startswith("col:") for asset in desired.assets)
    assert any(
        rel.relation_type_ref == config.relation_type_refs["table_fk"]
        for rel in desired.relationships
    )

    remote = adapter.read_remote_state(desired)
    assert remote.assets == ()
    plan = build_sync_plan(desired, remote)
    assert plan.creates
    assert all(action.action_type is not SyncActionType.REMOTE_ONLY for action in plan.creates)

    dry = execute_sync_plan(adapter, plan, apply=False)
    assert dry.success and dry.dry_run and dry.applied_count == 0
    assert not any(
        action["operation"] in {"create_asset", "update_asset", "create_relationship"}
        for action in adapter.actions
    )

    applied = execute_sync_plan(adapter, plan, apply=True)
    assert applied.success and applied.applied_count == len(plan.creates)

    second_plan = build_sync_plan(desired, adapter.read_remote_state(desired))
    assert second_plan.creates == ()
    assert second_plan.updates == ()
    second_apply = execute_sync_plan(adapter, second_plan, apply=True)
    assert second_apply.success and second_apply.applied_count == 0

    connection = _connect_autocommit(settings)
    original_comment = _read_column_comment(connection, "commerce", "customers", "email")
    mutated_comment = f"{original_comment or ''} [collibra-lifecycle]"
    try:
        _set_column_comment(
            connection,
            "commerce",
            "customers",
            "email",
            mutated_comment,
        )
        mutated_model = scanner.scan()
        mutated_desired = map_to_desired_state(
            MetadataInventory.from_model(mutated_model),
            config,
        )
        mutation_plan = build_sync_plan(
            mutated_desired,
            adapter.read_remote_state(mutated_desired),
        )
        assert len(mutation_plan.updates) == 1
        assert mutation_plan.creates == ()
        updated_local_id = mutation_plan.updates[0].local_id
        assert updated_local_id is not None
        assert updated_local_id.endswith("/customers/email")
        mutation_result = execute_sync_plan(adapter, mutation_plan, apply=True)
        assert mutation_result.success and mutation_result.applied_count == 1
    finally:
        _set_column_comment(
            connection,
            "commerce",
            "customers",
            "email",
            original_comment,
        )
        connection.close()
