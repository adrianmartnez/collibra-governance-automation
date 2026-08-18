"""Unit tests for stale-plan protection and target-context binding."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.cli import main
from governance.config import Settings
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
from governance.identity import remote_state_identity, target_context_identity
from governance.identity.hashing import ContentIdentity
from governance.integrations.collibra import MockCollibraAdapter
from governance.integrations.collibra.endpoint import normalize_base_url
from governance.integrations.collibra.models import CollibraRemoteState
from governance.plans import (
    SavedGovernancePlan,
    build_stale_result,
    identity_mismatch,
    load_saved_plan,
    version_mismatch,
    write_saved_plan,
)
from governance.plans.errors import STALE_RESULT_SCHEMA
from governance.plans.remote_identity import remote_state_identity_projection
from governance.plans.target_context import build_target_context_projection

CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"
POLICY_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "policies"


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
                                        description="orders table",
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


def _write_workspace(tmp_path: Path, *, policy_file: str | None = None) -> Path:
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if policy_file:
        policies = tmp_path / "policies"
        policies.mkdir(exist_ok=True)
        (policies / policy_file).write_text(
            (POLICY_FIXTURES / policy_file).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        files_block = f"  files:\n    - policies/{policy_file}"
    else:
        files_block = "  files: []"
    config = tmp_path / "governance.yaml"
    config.write_text(
        "\n".join(
            [
                'schema_version: "1"',
                "sources:",
                "  - id: primary",
                "    provider: postgresql",
                "    config:",
                "      source_name: governance-demo",
                "      connection:",
                "        database_url_env: DATABASE_URL",
                "targets:",
                "  - id: collibra",
                "    provider: collibra",
                "    config:",
                "      mode_env: COLLIBRA_MODE",
                "      mapping:",
                "        path: mapping.json",
                "      auth:",
                "        base_url_env: COLLIBRA_BASE_URL",
                "        username_env: COLLIBRA_USERNAME",
                "        password_env: COLLIBRA_PASSWORD",
                "policies:",
                files_block,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def _patch_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    values = {
        "DATABASE_URL": "postgresql://postgres:super-secret-password@localhost:5432/governance_demo",
        "COLLIBRA_MODE": "mock",
        "COLLIBRA_BASE_URL": "https://tenant-a.example.com",
        "COLLIBRA_USERNAME": "collibra-user",
        "COLLIBRA_PASSWORD": "collibra-secret-password",
        # Keep bearer unset by default so live basic-auth cases remain valid.
        "COLLIBRA_BEARER_TOKEN": "",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _patch_scanner(monkeypatch: pytest.MonkeyPatch, model: GovernanceModel | None = None) -> None:
    target = model if model is not None else _model()

    class FakeScanner:
        def __init__(self, settings) -> None:
            self.settings = settings

        def scan(self) -> GovernanceModel:
            return target

    monkeypatch.setattr("governance.cli.PostgresMetadataScanner", FakeScanner)


def _patch_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmanaged_assets: int = 0,
    unmanaged_relationships: int = 0,
) -> dict[str, object]:
    calls: dict[str, object] = {"count": 0, "read": 0, "writes": 0, "adapters": []}

    def factory(settings, mapping_config, *, transport=None):
        calls["count"] = int(calls["count"]) + 1
        adapter = MockCollibraAdapter(mapping_config)
        original_read = adapter.read_remote_state
        original_create = adapter.create_asset

        def tracked_read(desired):
            calls["read"] = int(calls["read"]) + 1
            remote = original_read(desired)
            return CollibraRemoteState(
                assets=remote.assets,
                relationships=remote.relationships,
                unmanaged_assets_ignored=unmanaged_assets,
                unmanaged_relationships_ignored=unmanaged_relationships,
            )

        def tracked_create(asset):
            calls["writes"] = int(calls["writes"]) + 1
            return original_create(asset)

        adapter.read_remote_state = tracked_read  # type: ignore[method-assign]
        adapter.create_asset = tracked_create  # type: ignore[method-assign]
        calls["adapters"].append(adapter)
        return adapter

    monkeypatch.setattr("governance.cli.build_collibra_adapter", factory)
    return calls


def _fake_identity(digest: str = "a" * 64) -> ContentIdentity:
    return ContentIdentity(
        algorithm="sha256",
        hashing_contract_version="1",
        digest=digest,
    )


def _rewrite_plan(plan: SavedGovernancePlan, path: Path, **changes) -> None:
    fields = {
        "sync_plan": plan.sync_plan,
        "config_identity": plan.config_identity,
        "snapshot_identity": plan.snapshot_identity,
        "policy_identity": plan.policy_identity,
        "mapping_identity": plan.mapping_identity,
        "target_context": plan.target_context,
        "target_context_identity": plan.target_context_identity,
        "remote_state_identity": plan.remote_state_identity,
        "planner_contract_version": plan.planner_contract_version,
        "scanner_contract_version": plan.scanner_contract_version,
        "plan_schema": plan.plan_schema,
        "plan_version": plan.plan_version,
    }
    fields.update(changes)
    write_saved_plan(SavedGovernancePlan(**fields), path)


def _generate_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    policy_file: str | None = None,
    capsys: pytest.CaptureFixture[str] | None = None,
) -> tuple[Path, Path]:
    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_adapter(monkeypatch)
    config = _write_workspace(tmp_path, policy_file=policy_file)
    plan_path = tmp_path / "plan.gplan"
    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(plan_path),
                "--format",
                "json",
            ]
        )
        == 0
    )
    if capsys is not None:
        capsys.readouterr()
    return config, plan_path


def test_stale_result_contract_helpers() -> None:
    mismatches = [
        identity_mismatch(
            category="policy",
            expected=_fake_identity("1" * 64),
            observed=_fake_identity("2" * 64),
            message="policy identity changed",
        ),
        version_mismatch(
            category="planner_contract",
            expected="1",
            observed="2",
            message="planner contract version changed",
        ),
        identity_mismatch(
            category="config",
            expected=_fake_identity("3" * 64),
            observed=_fake_identity("4" * 64),
            message="governance config identity changed",
        ),
    ]
    result = build_stale_result(mismatches)
    assert result["stale"] is True
    assert result["result_schema"] == STALE_RESULT_SCHEMA
    categories = [item["category"] for item in result["mismatches"]]
    assert categories == sorted(categories)


def test_stale_categories_exit_5_zero_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys=capsys)
    saved = load_saved_plan(plan_path)
    _rewrite_plan(
        saved,
        plan_path,
        snapshot_identity=_fake_identity("b" * 64),
        policy_identity=_fake_identity("c" * 64),
        config_identity=_fake_identity("d" * 64),
        mapping_identity=_fake_identity("e" * 64),
        planner_contract_version="999",
        scanner_contract_version="998",
    )
    adapter_calls = _patch_adapter(monkeypatch)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale"] is True
    categories = {item["category"] for item in payload["mismatches"]}
    assert {
        "snapshot",
        "policy",
        "config",
        "mapping",
        "planner_contract",
        "scanner_contract",
    } <= categories
    assert adapter_calls["writes"] == 0


def test_target_mismatch_skips_remote_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(
        monkeypatch,
        COLLIBRA_MODE="live",
        COLLIBRA_BASE_URL="https://tenant-a.example.com",
        COLLIBRA_BEARER_TOKEN="",
    )
    _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter(monkeypatch)
    config = _write_workspace(tmp_path)
    plan_path = tmp_path / "plan.gplan"
    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(plan_path),
                "--format",
                "json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    reads_after_plan = int(adapter_calls["read"])

    monkeypatch.setenv("COLLIBRA_BASE_URL", "https://tenant-b.example.com")
    adapter_calls = _patch_adapter(monkeypatch)
    code = main(["apply", str(plan_path), "--config", str(config), "--format", "json"])
    assert code == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale"] is True
    assert any(item["category"] == "target_context" for item in payload["mismatches"])
    assert adapter_calls["read"] == 0
    assert adapter_calls["writes"] == 0
    assert reads_after_plan >= 1


def test_unmanaged_counts_are_non_material(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = CollibraRemoteState()
    with_counts = CollibraRemoteState(
        unmanaged_assets_ignored=42,
        unmanaged_relationships_ignored=7,
    )
    assert remote_state_identity(remote_state_identity_projection(empty)) == remote_state_identity(
        remote_state_identity_projection(with_counts)
    )

    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys=capsys)
    # Plan was built with unmanaged=0; apply with different unmanaged counts should stay fresh.
    _patch_adapter(monkeypatch, unmanaged_assets=99, unmanaged_relationships=12)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 0


def test_credential_only_change_does_not_change_target_identity() -> None:
    def settings(password: str, timeout: float = 10.0) -> Settings:
        return Settings(
            postgres_host="localhost",
            postgres_port=5432,
            postgres_db="db",
            postgres_user="u",
            postgres_password="p",
            postgres_source_name="src",
            inventory_output_path="artifacts/x.json",
            collibra_mode="live",
            collibra_base_url="https://tenant.example.com/",
            collibra_username="user",
            collibra_password=password,
            collibra_bearer_token="token-a",
            collibra_timeout_seconds=timeout,
        )

    a = build_target_context_projection(settings("secret-1", 10.0))
    b = build_target_context_projection(settings("secret-2", 30.0))
    assert a == b
    assert target_context_identity(a) == target_context_identity(b)
    assert a["endpoint"] == normalize_base_url("https://tenant.example.com/")


def test_mode_change_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys=capsys)
    monkeypatch.setenv("COLLIBRA_MODE", "live")
    monkeypatch.setenv("COLLIBRA_BASE_URL", "https://tenant-a.example.com")
    monkeypatch.setenv("COLLIBRA_BEARER_TOKEN", "")
    adapter_calls = _patch_adapter(monkeypatch)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 5
    payload = json.loads(capsys.readouterr().out)
    assert any(item["category"] == "target_context" for item in payload["mismatches"])
    assert adapter_calls["read"] == 0


def test_generated_plan_public_target_context_matches_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from governance.plans.target_context import target_context_public

    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys=capsys)
    saved = load_saved_plan(plan_path)
    settings = Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="x",
        postgres_source_name="governance-demo",
        inventory_output_path="artifacts/x.json",
        collibra_mode="mock",
        collibra_base_url="https://tenant-a.example.com",
        collibra_username="collibra-user",
        collibra_password="collibra-secret-password",
        collibra_bearer_token="",
        collibra_timeout_seconds=10.0,
    )
    observed = target_context_public(build_target_context_projection(settings))
    assert dict(saved.target_context) == observed
    adapter_calls = _patch_adapter(monkeypatch)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 0
    assert adapter_calls["writes"] == 0


def test_tampered_public_target_mode_refuses_before_remote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys=capsys)
    saved = load_saved_plan(plan_path)
    assert saved.target_context["mode"] == "mock"
    _rewrite_plan(
        saved,
        plan_path,
        target_context={"provider": "collibra", "mode": "live"},
    )
    # Content identity is recomputed by write_saved_plan/to_json path.
    reloaded = load_saved_plan(plan_path)
    assert reloaded.target_context["mode"] == "live"
    assert reloaded.target_context_identity == saved.target_context_identity
    adapter_calls = _patch_adapter(monkeypatch)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostic_schema"] == "governance-plan-diagnostics"
    assert any(err["code"] == "target_context_inconsistent" for err in payload["errors"])
    assert adapter_calls["read"] == 0
    assert adapter_calls["writes"] == 0


def test_tampered_public_target_provider_refuses_before_remote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invalid provider fails closed (schema) before remote I/O / writes."""
    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys=capsys)
    saved = load_saved_plan(plan_path)
    # Bypass helpers that re-validate schema so we can persist an invalid provider.
    payload = saved.to_dict()
    payload["target_context"] = {"provider": "other", "mode": "mock"}
    without = {key: value for key, value in payload.items() if key != "content_identity"}
    from governance.identity.hashing import plan_identity

    payload["content_identity"] = plan_identity(without).to_dict()
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    adapter_calls = _patch_adapter(monkeypatch)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 4
    out = capsys.readouterr().out
    payload_out = json.loads(out)
    assert payload_out["diagnostic_schema"] == "governance-plan-diagnostics"
    assert payload_out["ok"] is False
    assert adapter_calls["read"] == 0
    assert adapter_calls["writes"] == 0


def test_empty_to_material_policy_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys=capsys)
    # Add a material policy file after plan generation.
    policies = tmp_path / "policies"
    policies.mkdir(exist_ok=True)
    (policies / "tables_require_description_warning.yaml").write_text(
        (POLICY_FIXTURES / "tables_require_description_warning.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "files: []",
            "files:\n    - policies/tables_require_description_warning.yaml",
        ),
        encoding="utf-8",
    )
    adapter_calls = _patch_adapter(monkeypatch)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 5
    payload = json.loads(capsys.readouterr().out)
    assert any(item["category"] == "policy" for item in payload["mismatches"])
    assert adapter_calls["writes"] == 0


def test_remote_state_mismatch_exit_5(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys=capsys)
    saved = load_saved_plan(plan_path)
    _rewrite_plan(saved, plan_path, remote_state_identity=_fake_identity("f" * 64))
    adapter_calls = _patch_adapter(monkeypatch)
    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 5
    payload = json.loads(capsys.readouterr().out)
    assert any(item["category"] == "remote_state" for item in payload["mismatches"])
    # Matching target_context still performs a remote read before declaring stale.
    assert int(adapter_calls["read"]) >= 1
    assert adapter_calls["writes"] == 0


def test_sync_id_env_value_change_is_stale_zero_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    uuid_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    uuid_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    _patch_env(
        monkeypatch,
        COLLIBRA_MODE="live",
        COLLIBRA_BASE_URL="https://tenant-a.example.com",
        COLLIBRA_BEARER_TOKEN="",
        COLLIBRA_EXECUTION_MODE="sync_v2",
        COLLIBRA_SYNCHRONIZATION_ID=uuid_a,
    )
    _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter(monkeypatch)
    config = _write_workspace(tmp_path)
    plan_path = tmp_path / "plan.gplan"
    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(plan_path),
                "--format",
                "json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    saved = load_saved_plan(plan_path)
    assert saved.target_context == {"provider": "collibra", "mode": "live"}

    monkeypatch.setenv("COLLIBRA_SYNCHRONIZATION_ID", uuid_b)
    adapter_calls = _patch_adapter(monkeypatch)
    code = main(["apply", str(plan_path), "--config", str(config), "--format", "json"])
    assert code == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale"] is True
    assert any(item["category"] == "target_context" for item in payload["mismatches"])
    assert adapter_calls["read"] == 0
    assert adapter_calls["writes"] == 0
