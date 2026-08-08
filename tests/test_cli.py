"""Unit tests for the governance CLI (no real PostgreSQL / Collibra network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance import __version__
from governance.cli import main
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
    CollibraAdapterError,
    CollibraMappingConfig,
    MockCollibraAdapter,
    load_mapping_config_file,
    mapping_contains_example_placeholders,
)
from governance.scanner import MetadataDiscoveryError


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


def _live_mapping_payload() -> dict[str, object]:
    return {
        "domain_ref": "domain-ref-1",
        "asset_type_refs": {
            "database": "asset-database",
            "schema": "asset-schema",
            "table": "asset-table",
            "column": "asset-column",
        },
        "relation_type_refs": {
            "database_schema": "rel-db-schema",
            "schema_table": "rel-schema-table",
            "table_column": "rel-table-column",
            "table_fk": "rel-table-fk",
        },
        "attribute_type_refs": {
            "local_id": "attr-local-id",
            "description": "attr-description",
            "owner": "attr-owner",
            "data_type": "attr-data-type",
            "nullable": "attr-nullable",
            "ordinal_position": "attr-ordinal",
        },
    }


def _write_mapping(path: Path, payload: dict[str, object] | None = None) -> Path:
    path.write_text(json.dumps(payload or _live_mapping_payload()), encoding="utf-8")
    return path


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    fixed = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "governance_demo",
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "super-secret-password",
        "COLLIBRA_MODE": "mock",
        "COLLIBRA_BASE_URL": "https://example.invalid",
        "COLLIBRA_BEARER_TOKEN": "secret-bearer-token",
    }
    fixed.update(overrides)

    def fake_load_settings(*, dotenv_path: str | None = ".env", environ=None):
        from governance.config import load_settings as real_load

        return real_load(dotenv_path=None, environ=fixed)

    monkeypatch.setattr("governance.cli.load_settings", fake_load_settings)


def _patch_scanner(monkeypatch: pytest.MonkeyPatch, model: GovernanceModel | None = None):
    calls = {"count": 0}
    target = model if model is not None else _model()

    class FakeScanner:
        def __init__(self, settings) -> None:
            self.settings = settings

        def scan(self) -> GovernanceModel:
            calls["count"] += 1
            return target

    monkeypatch.setattr("governance.cli.PostgresMetadataScanner", FakeScanner)
    return calls


def _patch_adapter_factory(monkeypatch: pytest.MonkeyPatch):
    calls = {"count": 0, "http": 0, "adapters": []}

    def factory(settings, mapping_config, *, transport=None):
        calls["count"] += 1
        adapter = MockCollibraAdapter(mapping_config)
        original_read = adapter.read_remote_state

        def tracked_read(desired):
            calls["http"] += 1
            return original_read(desired)

        adapter.read_remote_state = tracked_read  # type: ignore[method-assign]
        calls["adapters"].append(adapter)
        return adapter

    monkeypatch.setattr("governance.cli.build_collibra_adapter", factory)
    return calls


def test_help_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    help_out = capsys.readouterr().out
    assert "scan" in help_out
    assert "export" in help_out
    assert "diff" in help_out
    assert "sync" in help_out
    assert "config" in help_out

    assert main(["--version"]) == 0
    version_out = capsys.readouterr().out.strip()
    assert version_out == f"governance {__version__}"

    for command in ("scan", "export", "diff", "sync"):
        assert main([command, "--help"]) == 0
        assert command in capsys.readouterr().out


def test_bad_args_exit_2() -> None:
    assert main(["not-a-command"]) == 2
    assert main(["scan", "--unknown-flag"]) == 2


def test_scan_success_human_and_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    _patch_scanner(monkeypatch)

    assert main(["scan"]) == 0
    human = capsys.readouterr().out
    assert "source=governance-demo" in human
    assert "database=governance_demo" in human
    assert "schemas=1" in human
    assert "tables=2" in human
    assert "columns=3" in human
    assert "primary_keys=2" in human
    assert "foreign_keys=1" in human
    assert "relationships=1" in human

    assert main(["scan", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "source",
        "database",
        "schemas",
        "tables",
        "columns",
        "primary_keys",
        "foreign_keys",
        "relationships",
    }
    assert payload["source"] == "governance-demo"
    assert payload["tables"] == 2


def test_scan_discovery_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)

    class BoomScanner:
        def __init__(self, settings) -> None:
            pass

        def scan(self):
            raise MetadataDiscoveryError("PostgreSQL metadata discovery failed")

    monkeypatch.setattr("governance.cli.PostgresMetadataScanner", BoomScanner)
    assert main(["scan"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "PostgreSQL metadata discovery failed" in err


def test_export_default_and_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    default_path = tmp_path / "default-inventory.json"
    explicit_path = tmp_path / "explicit-inventory.json"
    _patch_settings(monkeypatch, INVENTORY_OUTPUT_PATH=str(default_path))
    _patch_scanner(monkeypatch)

    assert main(["export"]) == 0
    out = capsys.readouterr().out
    assert f"inventory_written={default_path}" in out
    assert default_path.is_file()
    json.loads(default_path.read_text(encoding="utf-8"))

    assert main(["export", "--output", str(explicit_path)]) == 0
    out = capsys.readouterr().out
    assert f"inventory_written={explicit_path}" in out
    assert explicit_path.is_file()


def test_export_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    _patch_scanner(monkeypatch)

    def boom_write(inventory, output_path):
        from governance.exporters import InventoryExportError

        raise InventoryExportError("Unable to write inventory to blocked")

    monkeypatch.setattr("governance.cli.write_inventory", boom_write)
    assert main(["export", "--output", str(tmp_path / "x.json")]) == 1
    assert "error: Unable to write inventory to blocked" in capsys.readouterr().err


def test_diff_mock_counts_and_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_adapter_factory(monkeypatch)

    assert main(["diff", "--mode", "mock"]) == 0
    human = capsys.readouterr().out
    assert "mode=mock" in human
    assert "writes=0" in human
    assert "create=" in human
    assert "CREATE " in human

    assert main(["diff", "--mode", "mock", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "mock"
    assert payload["writes"] == 0
    assert set(payload) >= {
        "mode",
        "create",
        "update",
        "unchanged",
        "remote_only",
        "writes",
        "actions",
    }
    assert payload["create"] > 0
    action_types = [item["action_type"] for item in payload["actions"]]
    assert "unchanged" not in action_types
    assert action_types == sorted(action_types)


def test_diff_live_requires_mapping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    scanner_calls = _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter_factory(monkeypatch)

    assert main(["diff", "--mode", "live"]) == 1
    err = capsys.readouterr().err
    assert "live mode requires --mapping-config" in err
    assert scanner_calls["count"] == 0
    assert adapter_calls["count"] == 0


def test_diff_invalid_mapping_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    scanner_calls = _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter_factory(monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")

    assert main(["diff", "--mode", "live", "--mapping-config", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "invalid Collibra mapping configuration" in err
    assert "{not-json" not in err
    assert scanner_calls["count"] == 0
    assert adapter_calls["count"] == 0


def test_sync_default_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter_factory(monkeypatch)

    assert main(["sync", "--mode", "mock"]) == 0
    out = capsys.readouterr().out
    assert "mode=mock" in out
    assert "dry_run=true" in out
    assert "applied=0" in out
    assert "success=true" in out
    assert adapter_calls["count"] == 1

    assert main(["sync", "--mode", "mock", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "mode",
        "dry_run",
        "create",
        "update",
        "unchanged",
        "remote_only",
        "applied",
        "success",
    }
    assert payload["mode"] == "mock"
    assert payload["dry_run"] is True
    assert payload["applied"] == 0
    assert payload["success"] is True


def test_sync_mock_apply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_adapter_factory(monkeypatch)

    assert main(["sync", "--mode", "mock", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "dry_run=false" in out
    assert "success=true" in out
    assert "applied=" in out
    applied_line = next(line for line in out.splitlines() if line.startswith("applied="))
    assert int(applied_line.split("=", 1)[1]) > 0


def test_live_apply_without_confirm_zero_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mapping = _write_mapping(tmp_path / "mapping.json")
    _patch_settings(monkeypatch)
    scanner_calls = _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter_factory(monkeypatch)

    assert main(["sync", "--mode", "live", "--mapping-config", str(mapping), "--apply"]) == 2
    err = capsys.readouterr().err
    assert "live apply requires --confirm-live" in err
    assert scanner_calls["count"] == 0
    assert adapter_calls["count"] == 0
    assert adapter_calls["http"] == 0


def test_confirm_live_without_apply_zero_io(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    scanner_calls = _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter_factory(monkeypatch)

    assert main(["sync", "--mode", "live", "--confirm-live"]) == 2
    assert "--confirm-live requires --apply" in capsys.readouterr().err
    assert scanner_calls["count"] == 0
    assert adapter_calls["count"] == 0
    assert adapter_calls["http"] == 0


def test_confirm_live_with_mock_mode_zero_io(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    scanner_calls = _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter_factory(monkeypatch)

    assert main(["sync", "--mode", "mock", "--apply", "--confirm-live"]) == 2
    assert "--confirm-live is only valid with --mode live" in capsys.readouterr().err
    assert scanner_calls["count"] == 0
    assert adapter_calls["count"] == 0
    assert adapter_calls["http"] == 0


def test_live_confirmed_apply_reaches_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mapping = _write_mapping(tmp_path / "mapping.json")
    _patch_settings(monkeypatch)
    _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter_factory(monkeypatch)

    code = main(
        [
            "sync",
            "--mode",
            "live",
            "--mapping-config",
            str(mapping),
            "--apply",
            "--confirm-live",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "mode=live" in out
    assert "dry_run=false" in out
    assert "success=true" in out
    assert adapter_calls["count"] == 1
    applied_line = next(line for line in out.splitlines() if line.startswith("applied="))
    assert int(applied_line.split("=", 1)[1]) > 0


def test_live_placeholder_mapping_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _live_mapping_payload()
    payload["domain_ref"] = "<tenant-domain-id>"
    mapping = _write_mapping(tmp_path / "sample-like.json", payload)
    _patch_settings(monkeypatch)
    scanner_calls = _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter_factory(monkeypatch)

    assert main(["diff", "--mode", "live", "--mapping-config", str(mapping)]) == 1
    err = capsys.readouterr().err
    assert "example placeholders" in err
    assert scanner_calls["count"] == 0
    assert adapter_calls["count"] == 0
    assert adapter_calls["http"] == 0


def test_mapping_file_loader_and_placeholder_helper(tmp_path: Path) -> None:
    mapping_path = _write_mapping(tmp_path / "ok.json")
    config = load_mapping_config_file(mapping_path)
    assert isinstance(config, CollibraMappingConfig)
    assert mapping_contains_example_placeholders(config) is False

    placeholder = _write_mapping(
        tmp_path / "ph.json",
        {**_live_mapping_payload(), "domain_ref": "<tenant-domain-id>"},
    )
    assert mapping_contains_example_placeholders(load_mapping_config_file(placeholder)) is True


def test_sample_mapping_example_parses_and_is_placeholder() -> None:
    sample = Path(__file__).resolve().parents[1] / "sample" / "collibra-mapping.example.json"
    config = load_mapping_config_file(sample)
    assert mapping_contains_example_placeholders(config) is True


def test_adapter_failure_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    _patch_scanner(monkeypatch)

    class BoomAdapter(MockCollibraAdapter):
        def read_remote_state(self, desired):
            raise CollibraAdapterError(
                "remote read failed",
                operation="read_remote_state",
                endpoint_path="/rest/2.0/assets",
            )

    monkeypatch.setattr(
        "governance.cli.build_collibra_adapter",
        lambda settings, mapping_config, *, transport=None: BoomAdapter(mapping_config),
    )
    assert main(["diff", "--mode", "mock"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "remote read failed" in err
    assert "secret-bearer-token" not in err
    assert "super-secret-password" not in err


def test_secrets_never_appear_on_streams(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_settings(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_adapter_factory(monkeypatch)

    assert main(["scan"]) == 0
    assert main(["diff", "--mode", "mock"]) == 0
    assert main(["sync", "--mode", "mock"]) == 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "super-secret-password" not in combined
    assert "secret-bearer-token" not in combined
    assert "Authorization" not in combined
