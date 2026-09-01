"""Unit tests for saved .gplan artifacts (round-trip, inspect, dry apply)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    CollibraAssetSpec,
    MockCollibraAdapter,
    SyncAction,
    SyncActionType,
    SyncObjectKind,
    SyncPlan,
)
from governance.integrations.collibra.endpoint import normalize_base_url
from governance.plans import (
    PlanIntegrityError,
    UnsupportedPlanVersionError,
    load_saved_plan,
    write_saved_plan,
)
from governance.plans.errors import CODE_IDENTITY, CODE_UNSUPPORTED

CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"


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


def _write_workspace(tmp_path: Path) -> Path:
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
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
                "  files: []",
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
        "COLLIBRA_BASE_URL": "https://example.invalid",
        "COLLIBRA_USERNAME": "collibra-user",
        "COLLIBRA_PASSWORD": "collibra-secret-password",
        "COLLIBRA_BEARER_TOKEN": "secret-bearer-token",
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


def _patch_adapter(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {"count": 0, "read": 0, "adapters": [], "writes": 0}

    def factory(settings, mapping_config, *, transport=None):
        calls["count"] = int(calls["count"]) + 1
        adapter = MockCollibraAdapter(mapping_config)
        original_read = adapter.read_remote_state
        original_create = adapter.create_asset
        original_update = adapter.update_asset
        original_rel = adapter.create_relationship

        def tracked_read(desired):
            calls["read"] = int(calls["read"]) + 1
            return original_read(desired)

        def tracked_create(asset):
            calls["writes"] = int(calls["writes"]) + 1
            return original_create(asset)

        def tracked_update(remote_id, asset, **kwargs):
            calls["writes"] = int(calls["writes"]) + 1
            return original_update(remote_id, asset, **kwargs)

        def tracked_rel(relationship, **kwargs):
            calls["writes"] = int(calls["writes"]) + 1
            return original_rel(relationship, **kwargs)

        adapter.read_remote_state = tracked_read  # type: ignore[method-assign]
        adapter.create_asset = tracked_create  # type: ignore[method-assign]
        adapter.update_asset = tracked_update  # type: ignore[method-assign]
        adapter.create_relationship = tracked_rel  # type: ignore[method-assign]
        calls["adapters"].append(adapter)
        return adapter

    monkeypatch.setattr("governance.cli.build_collibra_adapter", factory)
    return calls


def _generate_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str] | None = None,
) -> tuple[Path, Path]:
    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_adapter(monkeypatch)
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
    assert plan_path.is_file()
    if capsys is not None:
        capsys.readouterr()
    return config, plan_path


def test_sync_action_from_dict_round_trip() -> None:
    action = SyncAction(
        action_type=SyncActionType.CREATE,
        object_kind=SyncObjectKind.ASSET,
        local_id="db:governance-demo/governance_demo",
        reason="missing remotely",
        desired_asset=CollibraAssetSpec(
            local_id="db:governance-demo/governance_demo",
            name="governance_demo",
            asset_type_ref="mock:asset-type:database",
            domain_ref="mock:domain:governance",
        ),
    )
    restored = SyncAction.from_dict(action.to_dict())
    assert restored == action
    plan = SyncPlan(actions=(action,))
    assert SyncPlan.from_dict(plan.to_dict()) == plan


def test_delete_action_rejected() -> None:
    with pytest.raises(ValueError, match="DELETE"):
        SyncAction.from_dict(
            {
                "action_type": "delete",
                "object_kind": "asset",
                "local_id": "db:x",
                "remote_id": "remote-1",
                "reason": "should fail",
                "desired_asset": None,
                "desired_relationship": None,
                "changed_fields": [],
            }
        )
    assert "DELETE" not in SyncActionType.__members__


def test_plan_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    loaded = load_saved_plan(plan_path)
    again = tmp_path / "roundtrip.gplan"
    write_saved_plan(loaded, again)
    reloaded = load_saved_plan(again)
    assert reloaded.to_dict() == loaded.to_dict()
    assert again.read_text(encoding="utf-8") == plan_path.read_text(encoding="utf-8")


def test_plan_tamper_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["content_identity"]["digest"] = "0" * 64
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(PlanIntegrityError) as exc:
        load_saved_plan(plan_path)
    assert exc.value.errors[0].code == CODE_IDENTITY

    assert main(["plan", "inspect", str(plan_path), "--format", "json"]) == 4


def test_unsupported_plan_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    # Generated plans are v2; v1 and v2 remain supported — unsupported is v3+.
    loaded = load_saved_plan(plan_path)
    assert loaded.plan_version in {"1", "2"}
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["plan_version"] = "3"
    # identity will also fail if we keep old digest; rewrite with new version only for schema path
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(UnsupportedPlanVersionError) as exc:
        load_saved_plan(plan_path)
    assert exc.value.errors[0].code == CODE_UNSUPPORTED


def test_delete_in_gplan_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["actions"] = [
        {
            "action_type": "delete",
            "changed_fields": [],
            "desired_asset": None,
            "desired_relationship": None,
            "local_id": "db:x",
            "object_kind": "asset",
            "reason": "nope",
            "remote_id": "r1",
        }
    ]
    # Recompute is not needed — load should reject before/at action parse.
    # Still need valid content_identity shape; force mismatch after action failure path.
    from governance.identity.hashing import plan_identity

    without = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = plan_identity(without).to_dict()
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    code = main(["plan", "inspect", str(plan_path), "--format", "json"])
    assert code == 4
    # Ensure unsupported action classification is reachable via SyncAction.from_dict
    with pytest.raises(ValueError, match="DELETE"):
        SyncPlan.from_dict({"actions": payload["actions"]})


def test_plan_inspect_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    scanner_calls = {"count": 0}
    adapter_calls = _patch_adapter(monkeypatch)

    class BoomScanner:
        def __init__(self, settings) -> None:
            pass

        def scan(self):
            scanner_calls["count"] += 1
            raise AssertionError("inspect must not scan")

    monkeypatch.setattr("governance.cli.PostgresMetadataScanner", BoomScanner)
    assert main(["plan", "inspect", str(plan_path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == load_saved_plan(plan_path).to_dict()
    assert scanner_calls["count"] == 0
    assert adapter_calls["read"] == 0


def test_dry_apply_zero_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    adapter_calls = _patch_adapter(monkeypatch)
    build_calls = {"count": 0}

    def boom_build(*args, **kwargs):
        build_calls["count"] += 1
        raise AssertionError("apply must not rebuild sync plan")

    monkeypatch.setattr("governance.cli.build_sync_plan", boom_build)

    assert main(["apply", str(plan_path), "--config", str(config), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale"] is False
    assert payload["dry_run"] is True
    assert payload["success"] is True
    assert payload["applied_count"] == 0
    assert payload["failed_action"] is None
    assert payload["error"] is None
    assert payload["result_schema"] == "governance-apply-result"
    assert build_calls["count"] == 0
    assert adapter_calls["writes"] == 0


def test_apply_does_not_call_build_sync_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    adapter_calls = _patch_adapter(monkeypatch)
    build_calls = {"count": 0}

    def boom_build(*args, **kwargs):
        build_calls["count"] += 1
        raise AssertionError("apply must use saved actions only")

    monkeypatch.setattr("governance.cli.build_sync_plan", boom_build)
    assert (
        main(
            [
                "apply",
                str(plan_path),
                "--config",
                str(config),
                "--apply",
                "--format",
                "json",
            ]
        )
        == 0
    )
    assert build_calls["count"] == 0
    assert int(adapter_calls["writes"]) > 0


def test_no_secrets_in_gplan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    text = plan_path.read_text(encoding="utf-8")
    for secret in (
        "super-secret-password",
        "collibra-secret-password",
        "secret-bearer-token",
        "collibra-user",
        "https://example.invalid",
        "Authorization",
    ):
        assert secret not in text
    payload = json.loads(text)
    assert set(payload["target_context"]) == {"mode", "provider"}
    assert "endpoint" not in payload["target_context"]


def test_normalize_base_url_shared_for_target_context() -> None:
    from governance.config import Settings
    from governance.identity import target_context_identity
    from governance.integrations.collibra import live as live_module
    from governance.plans.target_context import build_target_context_projection

    assert live_module.normalize_base_url is normalize_base_url
    a = normalize_base_url("https://example.com/")
    b = normalize_base_url("https://example.com")
    assert a == b == "https://example.com"

    def settings_for(url: str) -> Settings:
        return Settings(
            postgres_host="localhost",
            postgres_port=5432,
            postgres_db="db",
            postgres_user="u",
            postgres_password="p",
            postgres_source_name="src",
            inventory_output_path="artifacts/x.json",
            collibra_mode="live",
            collibra_base_url=url,
            collibra_username="user",
            collibra_password="secret",
            collibra_bearer_token="token",
            collibra_timeout_seconds=10.0,
        )

    id_a = target_context_identity(build_target_context_projection(settings_for(a)))
    id_b = target_context_identity(build_target_context_projection(settings_for(b + "/")))
    assert id_a == id_b
    id_c = target_context_identity(
        build_target_context_projection(settings_for("https://other.example.com"))
    )
    assert id_a != id_c


def test_v2_assumptions_identity_mismatch_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assumptions = payload["reconciliation_assumptions"]
    if assumptions["actions"]:
        props = assumptions["actions"][0]["properties"]
        if props:
            props[0]["decision"] = {
                "state": "SINGLE_OBSERVATION",
                "reason": "SINGLE_OBSERVATION",
                "value_groups": [],
                "effective_value": "tampered",
            }
    payload["reconciliation_assumptions"] = assumptions
    from governance.identity.hashing import plan_identity

    without = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = plan_identity(without, plan_version="2").to_dict()
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(PlanIntegrityError) as exc:
        load_saved_plan(plan_path)
    assert exc.value.errors[0].code == CODE_IDENTITY
    assert exc.value.errors[0].path == "/reconciliation_assumptions_identity"


def test_v1_plan_load_unaffected_by_assumptions_identity_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from governance.identity.hashing import plan_identity
    from governance.plans import PLAN_VERSION

    _config, plan_path = _generate_plan(monkeypatch, tmp_path, capsys)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload.pop("reconciliation_assumptions", None)
    payload.pop("reconciliation_assumptions_identity", None)
    payload["plan_version"] = PLAN_VERSION
    without = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = plan_identity(without, plan_version=PLAN_VERSION).to_dict()
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    loaded = load_saved_plan(plan_path)
    assert loaded.plan_version == PLAN_VERSION
    assert loaded.reconciliation_assumptions is None
