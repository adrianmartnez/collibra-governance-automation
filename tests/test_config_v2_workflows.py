"""Operational CLI workflows for governance.yaml schema_version 2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    Schema,
    Table,
    make_column_id,
    make_database_id,
    make_datasource_id,
    make_foreign_key_id,
    make_primary_key_id,
    make_schema_id,
    make_table_id,
)
from governance.identity import target_context_identity
from governance.integrations.collibra import MockCollibraAdapter
from governance.orchestration.operation_runtime import (
    load_operation_runtime,
    scan_model_from_runtime,
    settings_for_operation,
    target_context_projection_for_runtime,
)
from governance.orchestration.registry import build_provider_registry
from governance.plans.target_context import build_target_context_projection
from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRuntimeContext,
)

CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"
MAPPING_INLINE: dict[str, Any] = json.loads(
    (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8")
)


def _minimal_model(source: str = "governance-demo") -> GovernanceModel:
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
        relationships=(),
    )


def _write_v2_config(
    tmp_path: Path,
    *,
    sources: list[dict[str, Any]] | None = None,
    targets: list[dict[str, Any]] | None = None,
    authority: dict[str, Any] | None = None,
) -> Path:
    import yaml

    if sources is None:
        sources = [
            {
                "id": "primary",
                "provider": "postgresql",
                "config": {
                    "source_name": "governance-demo",
                    "database_url": {"$env": "DATABASE_URL"},
                },
            }
        ]
    if targets is None:
        targets = [
            {
                "id": "collibra",
                "provider": "collibra",
                "config": {
                    "mode": {"$env": "COLLIBRA_MODE"},
                    "mapping": dict(MAPPING_INLINE),
                    "base_url": {"$env": "COLLIBRA_BASE_URL"},
                    "password": {"$env": "COLLIBRA_PASSWORD"},
                },
            }
        ]
    document: dict[str, Any] = {
        "schema_version": "2",
        "sources": sources,
        "targets": targets,
        "policies": {"files": []},
    }
    if authority is not None:
        document["authority"] = authority
    path = tmp_path / "governance.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _write_v1_sync_v2_config(tmp_path: Path) -> Path:
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = tmp_path / "governance-v1.yaml"
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
                "      execution_mode_env: COLLIBRA_EXECUTION_MODE",
                "      mapping:",
                "        path: mapping.json",
                "      auth:",
                "        base_url_env: COLLIBRA_BASE_URL",
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
        "DATABASE_URL": "postgresql://postgres:secret@localhost:5432/governance_demo",
        "PGPORT": "5432",
        "PGPASSWORD": "secret",
        "COLLIBRA_MODE": "mock",
        "COLLIBRA_BASE_URL": "https://example.invalid",
        "COLLIBRA_PASSWORD": "collibra-secret",
        "COLLIBRA_EXECUTION_MODE": "sync_v2",
        "COLLIBRA_TIMEOUT_SECONDS": "10",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _patch_postgres_scanner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: GovernanceModel | None = None,
    boom: bool = False,
) -> dict[str, int]:
    calls = {"count": 0}
    expected = model if model is not None else _minimal_model()

    class _Scanner:
        def __init__(self, _params: object) -> None:
            pass

        def scan(self) -> GovernanceModel:
            calls["count"] += 1
            if boom:
                raise AssertionError("scanner must not run")
            return expected

    monkeypatch.setattr(
        "governance.providers.builtins.postgresql.PostgresMetadataScanner",
        _Scanner,
    )
    return calls


def _patch_collibra_adapter(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"build": 0, "mutation": 0}

    def factory(_settings: object, mapping_config: object, *, transport: object = None) -> object:
        calls["build"] += 1
        return MockCollibraAdapter(mapping_config)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "governance.providers.builtins.collibra.build_collibra_adapter",
        factory,
    )
    return calls


def test_v2_scan_via_metadata_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_v2_config(tmp_path)
    scanner = _patch_postgres_scanner(monkeypatch)

    assert main(["scan", "--config", str(config), "--json"]) == 0
    assert scanner["count"] == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["tables"] == 2
    assert payload["source"] == "governance-demo"


def test_v2_external_metadata_discovery_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected = _minimal_model(source="external-demo")

    def factory(_context: ProviderRuntimeContext) -> object:
        class _Cap:
            def discover(self) -> GovernanceModel:
                return expected

        return _Cap()

    registration = ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="acme.external",
            display_name="External",
            provider_version="1.0.0",
            sdk_compatibility="==1",
            capabilities=(CapabilityId.METADATA_DISCOVERY,),
        ),
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.METADATA_DISCOVERY,
                factory=factory,
            ),
        ),
    )

    def _registry(**_kwargs: object) -> ProviderRegistry:
        registry = build_provider_registry(discover_external=False)
        registry.register(registration)
        return registry

    monkeypatch.setattr(
        "governance.orchestration.operation_runtime.build_provider_registry",
        _registry,
    )
    config = _write_v2_config(
        tmp_path,
        sources=[
            {
                "id": "external",
                "provider": "acme.external",
                "config": {"option": "value"},
            }
        ],
        targets=[
            {
                "id": "collibra",
                "provider": "collibra",
                "config": {
                    "mode": "mock",
                    "mapping": dict(MAPPING_INLINE),
                },
            }
        ],
    )
    runtime = load_operation_runtime(str(config), profile=None)
    assert runtime.kind == "2"
    model = scan_model_from_runtime(runtime)
    assert model is expected


def test_v2_plan_mock_collibra_inline_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_v2_config(tmp_path)
    _patch_postgres_scanner(monkeypatch)
    adapter = _patch_collibra_adapter(monkeypatch)
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
    assert adapter["build"] >= 1
    assert plan_path.is_file()
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["plan_version"] == "2"
    assert set(payload["target_context"]) == {"mode", "provider"}


def test_v2_preflight_mock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_v2_config(tmp_path)
    _patch_postgres_scanner(monkeypatch, boom=True)
    adapter = _patch_collibra_adapter(monkeypatch)

    code = main(["preflight", "--config", str(config), "--format", "json"])
    capsys.readouterr()
    assert code in {0, 1}
    assert adapter["build"] == 0


def test_v2_unknown_provider_fails_before_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_env(monkeypatch)
    config = _write_v2_config(
        tmp_path,
        sources=[
            {
                "id": "primary",
                "provider": "unknown.vendor",
                "config": {"token": "x"},
            }
        ],
    )
    scanner = _patch_postgres_scanner(monkeypatch, boom=True)

    code = main(["scan", "--config", str(config), "--json"])
    assert code in {1, 4}
    assert scanner["count"] == 0


def test_v2_missing_capability_diagnostic() -> None:
    from governance.providers import ProviderRegistryError

    registry = build_provider_registry(discover_external=False)
    with pytest.raises(ProviderRegistryError) as exc_info:
        registry.require_capabilities(
            "postgresql",
            (
                CapabilityId.METADATA_DISCOVERY,
                CapabilityId.PROPERTY_OBSERVATIONS,
            ),
        )
    missing = [item for item in exc_info.value.errors if item.code == "missing_capability"]
    assert missing
    assert any("property_observations" in item.path for item in missing)


def test_v2_ambiguous_metadata_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    duplicate_source = {
        "id": "primary",
        "provider": "postgresql",
        "config": {
            "source_name": "governance-demo",
            "database_url": {"$env": "DATABASE_URL"},
        },
    }
    config = _write_v2_config(
        tmp_path,
        sources=[duplicate_source, dict(duplicate_source, id="secondary")],
    )
    scanner = _patch_postgres_scanner(monkeypatch, boom=True)

    code = main(["scan", "--config", str(config), "--json"])
    capsys.readouterr()
    assert code in {1, 4}
    assert scanner["count"] == 0


def test_v2_ambiguous_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    target = {
        "id": "collibra-a",
        "provider": "collibra",
        "config": {
            "mode": {"$env": "COLLIBRA_MODE"},
            "mapping": dict(MAPPING_INLINE),
            "base_url": {"$env": "COLLIBRA_BASE_URL"},
            "password": {"$env": "COLLIBRA_PASSWORD"},
        },
    }
    config = _write_v2_config(
        tmp_path,
        targets=[target, dict(target, id="collibra-b")],
    )
    _patch_postgres_scanner(monkeypatch)
    adapter = _patch_collibra_adapter(monkeypatch)

    code = main(["diff", "--config", str(config), "--mode", "mock", "--json"])
    capsys.readouterr()
    assert code in {1, 4}
    assert adapter["build"] == 0


def test_v2_settings_path_message_removed() -> None:
    cli_source = (Path(__file__).resolve().parents[1] / "src" / "governance" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert "_V2_SETTINGS_PATH_MESSAGE" not in cli_source


def test_v2_config_validate_invalid_authority_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_v2_config(
        tmp_path,
        authority={"files": ["authority/missing.yaml"]},
    )
    _patch_postgres_scanner(monkeypatch, boom=True)

    code = main(["config", "validate", "--config", str(config), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["errors"]


def test_sync_dry_run_authorized_mutation_calls_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    config = _write_v2_config(tmp_path)
    _patch_postgres_scanner(monkeypatch)
    _patch_collibra_adapter(monkeypatch)
    mutation_calls = {"count": 0}

    def boom(*_args: object, **_kwargs: object) -> object:
        mutation_calls["count"] += 1
        raise AssertionError("authorized_mutation must not run on dry-run sync")

    monkeypatch.setattr(
        "governance.orchestration.targets.invoke_authorized_mutation",
        boom,
    )

    code = main(["sync", "--config", str(config), "--mode", "mock", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["dry_run"] is True
    assert mutation_calls["count"] == 0


def test_collibra_timeout_env_valid_and_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    target = {
        "id": "collibra",
        "provider": "collibra",
        "config": {
            "mode": {"$env": "COLLIBRA_MODE"},
            "mapping": dict(MAPPING_INLINE),
            "base_url": {"$env": "COLLIBRA_BASE_URL"},
            "password": {"$env": "COLLIBRA_PASSWORD"},
            "timeout_seconds": {"$env": "COLLIBRA_TIMEOUT_SECONDS"},
        },
    }
    config = _write_v2_config(tmp_path, targets=[target])
    _patch_postgres_scanner(monkeypatch)
    adapter = _patch_collibra_adapter(monkeypatch)

    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(tmp_path / "ok.gplan"),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert adapter["build"] >= 1

    monkeypatch.setenv("COLLIBRA_TIMEOUT_SECONDS", "not-a-number")
    scanner = _patch_postgres_scanner(monkeypatch, boom=True)
    adapter = _patch_collibra_adapter(monkeypatch)
    assert (
        main(
            [
                "plan",
                "--config",
                str(config),
                "--output",
                str(tmp_path / "bad.gplan"),
                "--format",
                "json",
            ]
        )
        == 4
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(err["code"] == "runtime_configuration_invalid" for err in payload["errors"])
    assert any("/targets/0/config/timeout_seconds" in err["path"] for err in payload["errors"])
    assert scanner["count"] == 0
    assert adapter["build"] == 0


def test_postgres_port_env_valid_and_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources = [
        {
            "id": "primary",
            "provider": "postgresql",
            "config": {
                "source_name": "governance-demo",
                "host": "localhost",
                "port": {"$env": "PGPORT"},
                "database": "governance_demo",
                "user": "postgres",
                "password": {"$env": "PGPASSWORD"},
            },
        }
    ]
    _patch_env(monkeypatch)
    config = _write_v2_config(tmp_path, sources=sources)
    _patch_postgres_scanner(monkeypatch)
    assert main(["scan", "--config", str(config), "--json"]) == 0
    capsys.readouterr()

    monkeypatch.setenv("PGPORT", "not-an-int")
    scanner = _patch_postgres_scanner(monkeypatch, boom=True)
    _patch_collibra_adapter(monkeypatch)
    assert main(["diff", "--config", str(config), "--mode", "mock", "--json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert any(err["path"] == "/sources/0/config/port" for err in payload["errors"])
    assert scanner["count"] == 0


def test_unknown_key_timeout_typo_rejected() -> None:
    from governance.providers import ProviderError

    registry = build_provider_registry(discover_external=False)
    registration = registry.get("collibra")
    assert registration.config_validator is not None
    with pytest.raises(ProviderError) as exc_info:
        registration.config_validator.validate(
            {
                "mode": "mock",
                "mapping": dict(MAPPING_INLINE),
                "timeuot_seconds": 10,
            }
        )
    assert any("timeuot_seconds" in item.message for item in exc_info.value.errors)


def test_v1_v2_sync_v2_target_context_identity_equivalence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_env(monkeypatch)
    v1_path = _write_v1_sync_v2_config(tmp_path)
    v2_path = _write_v2_config(
        tmp_path,
        targets=[
            {
                "id": "collibra",
                "provider": "collibra",
                "config": {
                    "mode": {"$env": "COLLIBRA_MODE"},
                    "execution_mode": "sync_v2",
                    "mapping": dict(MAPPING_INLINE),
                    "base_url": {"$env": "COLLIBRA_BASE_URL"},
                    "password": {"$env": "COLLIBRA_PASSWORD"},
                },
            }
        ],
    )
    v1_runtime = load_operation_runtime(str(v1_path), profile=None)
    v2_runtime = load_operation_runtime(str(v2_path), profile=None)
    assert v1_runtime.kind == "1"
    assert v2_runtime.kind == "2"
    v1_projection = build_target_context_projection(settings_for_operation(v1_runtime))
    v2_projection = target_context_projection_for_runtime(v2_runtime)
    assert target_context_identity(v1_projection) == target_context_identity(v2_projection)


def test_v2_reconciliation_jobs_from_odcs_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_env(monkeypatch)
    import yaml

    from governance.orchestration.selection import compose_v2_reconciliation_jobs

    doc = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": "contract-orders",
        "version": "1.0.0",
        "status": "active",
        "name": "Orders Contract",
    }
    odcs_name = "contract.odcs.yaml"
    (tmp_path / odcs_name).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    config = _write_v2_config(
        tmp_path,
        sources=[
            {
                "id": "primary",
                "provider": "postgresql",
                "config": {
                    "source_name": "governance-demo",
                    "database_url": {"$env": "DATABASE_URL"},
                },
            },
            {
                "id": "contracts",
                "provider": "odcs",
                "config": {
                    "path": odcs_name,
                    "namespace": "governance-demo",
                },
            },
        ],
    )
    runtime = load_operation_runtime(str(config), profile=None)
    assert runtime.resolved is not None
    jobs = compose_v2_reconciliation_jobs(runtime.resolved, runtime.registry)
    provider_ids = {job.provider_id for job in jobs}
    assert provider_ids == {"odcs"}


def test_v2_impact_graph_from_odcs_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_env(monkeypatch)
    import yaml

    from governance.orchestration.selection import compose_v2_impact_jobs
    from governance.orchestration.sources import run_impact_graph

    doc = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": "contract-orders",
        "version": "1.0.0",
        "status": "active",
        "name": "Orders Contract",
    }
    odcs_name = "contract.odcs.yaml"
    (tmp_path / odcs_name).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    config = _write_v2_config(
        tmp_path,
        sources=[
            {
                "id": "contracts",
                "provider": "odcs",
                "config": {
                    "path": odcs_name,
                    "namespace": "governance-demo",
                },
            },
        ],
    )
    runtime = load_operation_runtime(str(config), profile=None)
    assert runtime.resolved is not None
    jobs = compose_v2_impact_jobs(runtime.resolved, runtime.registry)
    assert len(jobs) == 1
    graph = run_impact_graph(jobs)
    assert graph.nodes
