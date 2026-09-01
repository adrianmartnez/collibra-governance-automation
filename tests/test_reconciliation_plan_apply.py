"""Integration-style plan/apply reconciliation tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import governance
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
from governance.identity.hashing import plan_identity
from governance.integrations.collibra import MockCollibraAdapter
from governance.integrations.dbt.validate import SUPPORTED_MANIFEST_SCHEMA_URI
from governance.plans import PLAN_VERSION, PLAN_VERSION_V2, load_saved_plan

CONFIG_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml"
V12 = SUPPORTED_MANIFEST_SCHEMA_URI


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


def _write_workspace(
    tmp_path: Path,
    *,
    authority_files: list[str] | None = None,
) -> Path:
    (tmp_path / "mapping.json").write_text(
        (CONFIG_FIXTURES / "mapping.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    auth_block = ["authority:", "  files: []"]
    if authority_files is not None:
        auth_block = ["authority:", "  files:"] + [f"    - {name}" for name in authority_files]
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
                "policies:",
                "  files: []",
                *auth_block,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config


def _write_authority(path: Path, *, provider_type: str, property_path: str = "/description") -> Path:
    import yaml

    document = {
        "authority_schema": "governance-authority",
        "authority_version": "1",
        "rules": [
            {
                "id": f"table-desc-{provider_type}",
                "select": {"kind": "table", "property": property_path},
                "authority": {"provider_type": provider_type},
            }
        ],
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:super-secret-password@localhost:5432/governance_demo",
    )
    monkeypatch.setenv("COLLIBRA_MODE", "mock")
    monkeypatch.setenv("COLLIBRA_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("COLLIBRA_USERNAME", "collibra-user")
    monkeypatch.setenv("COLLIBRA_PASSWORD", "collibra-secret-password")


def _patch_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _model()

    class FakeScanner:
        def __init__(self, settings) -> None:
            self.settings = settings

        def scan(self) -> GovernanceModel:
            return target

    monkeypatch.setattr("governance.cli.PostgresMetadataScanner", FakeScanner)


def _patch_adapter(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {"count": 0, "read": 0, "writes": 0}

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
        return adapter

    monkeypatch.setattr("governance.cli.build_collibra_adapter", factory)
    return calls


def _generate_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path, int]:
    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    _patch_adapter(monkeypatch)
    config = _write_workspace(tmp_path)
    plan_path = tmp_path / "plan.gplan"
    code = main(
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
    return config, plan_path, code


def _dbt_manifest(*, description: str, unique_suffix: str = "") -> dict[str, Any]:
    uid = f"model.pkg.customers{unique_suffix}"
    return {
        "metadata": {
            "dbt_schema_version": V12,
            "dbt_version": "1.10.0",
            "generated_at": "2024-01-01T00:00:00.000000Z",
            "invocation_id": f"inv-{unique_suffix or 'a'}",
        },
        "nodes": {
            uid: {
                "unique_id": uid,
                "resource_type": "model",
                "name": "customers",
                "package_name": "pkg",
                "database": "governance_demo",
                "schema": "commerce",
                "alias": "customers",
                "fqn": ["pkg", "customers"],
                "config": {"materialized": "table"},
                "columns": {
                    "customer_id": {
                        "name": "customer_id",
                        "description": "id",
                        "meta": {},
                        "tags": [],
                    }
                },
                "tags": [],
                "meta": {},
                "description": description,
            }
        },
        "sources": {},
        "parent_map": {},
        "disabled": {},
    }


def test_package_version_1_3_0() -> None:
    assert governance.__version__ == "1.3.0"


def test_v1_plan_with_source_flag_exit_5_loaders_not_called(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, plan_path, code = _generate_plan(monkeypatch, tmp_path)
    assert code == 0
    capsys.readouterr()

    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload.pop("reconciliation_assumptions", None)
    payload.pop("reconciliation_assumptions_identity", None)
    payload["plan_version"] = PLAN_VERSION
    without = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = plan_identity(without, plan_version=PLAN_VERSION).to_dict()
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert load_saved_plan(plan_path).plan_version == PLAN_VERSION

    compose_calls = {"count": 0}
    odcs_calls = {"count": 0}

    def boom_compose(*args, **kwargs):
        compose_calls["count"] += 1
        raise AssertionError("compose must not run for v1 + source flags")

    def boom_odcs(*args, **kwargs):
        odcs_calls["count"] += 1
        raise AssertionError("odcs loader must not run for v1 + source flags")

    monkeypatch.setattr("governance.cli.compose_reconciliation_sources", boom_compose)
    monkeypatch.setattr(
        "governance.reconciliation.sources.load_odcs_graph_with_observations",
        boom_odcs,
    )
    fake_odcs = tmp_path / "unused-contract.json"
    fake_odcs.write_text("{}", encoding="utf-8")

    code = main(
        [
            "apply",
            str(plan_path),
            "--config",
            str(config),
            "--odcs",
            str(fake_odcs),
            "--format",
            "json",
        ]
    )
    assert code == 5
    out = json.loads(capsys.readouterr().out)
    assert out["stale"] is True
    assert compose_calls["count"] == 0
    assert odcs_calls["count"] == 0


def test_v2_plan_generate_has_assumptions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config, plan_path, code = _generate_plan(monkeypatch, tmp_path)
    assert code == 0
    capsys.readouterr()
    saved = load_saved_plan(plan_path)
    assert saved.plan_version == PLAN_VERSION_V2
    assert saved.reconciliation_assumptions is not None
    assert saved.reconciliation_assumptions["assumptions_schema"] == (
        "governance-reconciliation-assumptions"
    )
    assert saved.reconciliation_assumptions_identity is not None
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["plan_version"] == "2"
    assert "reconciliation_assumptions" in payload


def test_unresolved_relevant_conflict_blocks_plan_exit_4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter(monkeypatch)
    config = _write_workspace(tmp_path)

    left = tmp_path / "customers-a.json"
    right = tmp_path / "customers-b.json"
    left.write_text(
        json.dumps(_dbt_manifest(description="desc-a", unique_suffix="-a")),
        encoding="utf-8",
    )
    right.write_text(
        json.dumps(_dbt_manifest(description="desc-b", unique_suffix="-b")),
        encoding="utf-8",
    )
    plan_path = tmp_path / "blocked.gplan"
    code = main(
        [
            "plan",
            "--config",
            str(config),
            "--output",
            str(plan_path),
            "--dbt-manifest",
            str(left),
            "--dbt-manifest",
            str(right),
            "--format",
            "json",
        ]
    )
    assert code == 4
    assert not plan_path.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["diagnostic_schema"] == "governance-reconciliation-diagnostics"
    assert any(
        item["code"] == "unresolved_property_conflict" for item in payload["errors"]
    )
    assert int(adapter_calls["writes"]) == 0


def test_resolved_conflict_plan_uses_authorized_data_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dbt vs openlineage data_type disagreement resolves via authority into the plan."""
    import yaml

    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter(monkeypatch)
    auth = tmp_path / "auth.yaml"
    auth.write_text(
        yaml.safe_dump(
            {
                "authority_schema": "governance-authority",
                "authority_version": "1",
                "rules": [
                    {
                        "id": "col-type-dbt",
                        "select": {
                            "kind": "column",
                            "property": "/attributes/data_type",
                        },
                        "authority": {"provider_type": "dbt"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config = _write_workspace(tmp_path, authority_files=["auth.yaml"])

    dbt_doc = _dbt_manifest(description="customers table")
    dbt_doc["nodes"]["model.pkg.customers"]["columns"]["customer_id"]["data_type"] = (
        "uuid"
    )
    dbt_path = tmp_path / "manifest.json"
    dbt_path.write_text(json.dumps(dbt_doc), encoding="utf-8")

    ol_event = {
        "eventTime": "2024-01-01T00:00:00Z",
        "producer": "https://example.com/producer/1.0",
        "schemaURL": (
            "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/DatasetEvent"
        ),
        "dataset": {
            "namespace": "postgres://host",
            "name": "governance_demo.commerce.customers",
            "facets": {
                "hierarchy": {
                    "_producer": "https://facet/hierarchy",
                    "_schemaURL": (
                        "https://openlineage.io/spec/facets/1-0-0/"
                        "HierarchyDatasetFacet.json"
                    ),
                    "hierarchy": [
                        {"type": "DATABASE", "name": "governance_demo"},
                        {"type": "SCHEMA", "name": "commerce"},
                        {"type": "TABLE", "name": "customers"},
                    ],
                },
                "schema": {
                    "_producer": "https://facet/schema",
                    "_schemaURL": (
                        "https://openlineage.io/spec/facets/1-1-1/"
                        "SchemaDatasetFacet.json"
                    ),
                    "fields": [{"name": "customer_id", "type": "varchar"}],
                },
            },
        },
    }
    ol_path = tmp_path / "ol.json"
    ol_path.write_text(json.dumps([ol_event]), encoding="utf-8")

    plan_path = tmp_path / "resolved.gplan"
    code = main(
        [
            "plan",
            "--config",
            str(config),
            "--output",
            str(plan_path),
            "--dbt-manifest",
            str(dbt_path),
            "--openlineage",
            str(ol_path),
            "--format",
            "json",
        ]
    )
    assert code == 0, capsys.readouterr().out
    saved = load_saved_plan(plan_path)
    assert saved.reconciliation_assumptions is not None
    props = []
    for action in saved.reconciliation_assumptions["actions"]:
        if action["local_id"].endswith("/customer_id"):
            props.extend(action["properties"])
    data_types = [p for p in props if p["property"] == "/attributes/data_type"]
    assert data_types
    decision = data_types[0]["decision"]
    assert decision is not None
    assert decision["state"] == "RESOLVED_BY_AUTHORITY"
    assert decision["effective_value"] == "uuid"
    assert int(adapter_calls["writes"]) == 0


def test_unrelated_external_conflict_does_not_block_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unresolved conflict on off-PG physical object must not block the plan."""
    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    adapter_calls = _patch_adapter(monkeypatch)
    config = _write_workspace(tmp_path)

    ghost_event = {
        "eventTime": "2024-01-01T00:00:00Z",
        "producer": "https://example.com/producer/1.0",
        "schemaURL": (
            "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/DatasetEvent"
        ),
        "dataset": {
            "namespace": "postgres://host",
            "name": "other.ghost.table",
            "facets": {
                "hierarchy": {
                    "_producer": "https://facet/hierarchy",
                    "_schemaURL": (
                        "https://openlineage.io/spec/facets/1-0-0/"
                        "HierarchyDatasetFacet.json"
                    ),
                    "hierarchy": [
                        {"type": "DATABASE", "name": "other"},
                        {"type": "SCHEMA", "name": "ghost"},
                        {"type": "TABLE", "name": "table"},
                    ],
                },
                "ownership": {
                    "_producer": "https://facet/ownership",
                    "_schemaURL": (
                        "https://openlineage.io/spec/facets/1-0-0/"
                        "OwnershipDatasetFacet.json"
                    ),
                    "owners": [{"name": "alpha", "type": "USER"}],
                },
            },
        },
    }
    ghost_b_event = json.loads(json.dumps(ghost_event))
    ghost_b_event["dataset"]["facets"]["ownership"]["owners"] = [
        {"name": "beta", "type": "USER"}
    ]
    ghost_b_event["dataset"]["facets"]["ownership"]["_producer"] = (
        "https://facet/ownership-b"
    )
    ghost_a = tmp_path / "ghost-a.json"
    ghost_b = tmp_path / "ghost-b.json"
    ghost_a.write_text(json.dumps([ghost_event]), encoding="utf-8")
    ghost_b.write_text(json.dumps([ghost_b_event]), encoding="utf-8")

    plan_path = tmp_path / "ok.gplan"
    code = main(
        [
            "plan",
            "--config",
            str(config),
            "--output",
            str(plan_path),
            "--openlineage",
            str(ghost_a),
            "--openlineage",
            str(ghost_b),
            "--format",
            "json",
        ]
    )
    assert code == 0, capsys.readouterr().out
    assert plan_path.exists()
    assert int(adapter_calls["writes"]) == 0


def test_apply_stale_when_relevant_observation_appears_zero_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, plan_path, code = _generate_plan(monkeypatch, tmp_path)
    assert code == 0
    capsys.readouterr()
    adapter_calls = _patch_adapter(monkeypatch)

    dbt_path = tmp_path / "later.json"
    dbt_path.write_text(
        json.dumps(_dbt_manifest(description="new-desc-from-dbt")),
        encoding="utf-8",
    )
    code = main(
        [
            "apply",
            str(plan_path),
            "--config",
            str(config),
            "--dbt-manifest",
            str(dbt_path),
            "--format",
            "json",
        ]
    )
    assert code == 5
    out = json.loads(capsys.readouterr().out)
    assert out["stale"] is True
    assert any(item["category"] == "reconciliation" for item in out["mismatches"])
    assert int(adapter_calls["writes"]) == 0


def test_apply_stale_when_authority_materially_changes_zero_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Material authority change that flips the winner must stale apply before writes."""
    from governance.domain.authority import (
        AuthorityDeclaration,
        AuthorityRuleKey,
        AuthoritySelector,
        AuthorityTarget,
        NormalizedAuthorityPolicySet,
        NormalizedAuthorityRule,
    )
    from governance.domain.conflicts import analyze_property_conflicts
    from governance.domain.graph import (
        NODE_KIND_DATA_SOURCE,
        NODE_KIND_DATASET,
        NODE_KIND_TABLE,
        GraphNodeIdentity,
        ProvenanceRecord,
    )
    from governance.domain.observations import (
        PropertyObservation,
        PropertyObservationSet,
        PropertyPath,
    )
    from governance.identity.hashing import plan_identity
    from governance.reconciliation.assumptions import (
        assumptions_content_identity,
        recompute_assumptions_on_saved_boundary,
    )
    from governance.reconciliation.sources import ReconciliationSourceBundle

    ns = "governance-demo"
    ds = GraphNodeIdentity(ns, NODE_KIND_DATA_SOURCE, "governance_demo")
    dataset = GraphNodeIdentity(ns, NODE_KIND_DATASET, "commerce", parent=ds)
    identity = GraphNodeIdentity(ns, NODE_KIND_TABLE, "customers", parent=dataset)
    path = PropertyPath(("description",))
    observations = PropertyObservationSet.from_observations(
        (
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="from-odcs",
                provenance=(
                    ProvenanceRecord(
                        provider_type="odcs",
                        source_ref="c1",
                        source_version="1.0",
                        observation_mode="declared",
                    ),
                ),
            ),
            PropertyObservation(
                object_identity=identity,
                property_path=path,
                value="from-dbt",
                provenance=(
                    ProvenanceRecord(
                        provider_type="dbt",
                        source_ref="m",
                        source_version="1.0",
                        observation_mode="declared",
                    ),
                ),
            ),
        )
    )

    def _authority(provider: str) -> NormalizedAuthorityPolicySet:
        return NormalizedAuthorityPolicySet(
            rules=(
                NormalizedAuthorityRule(
                    key=AuthorityRuleKey(
                        selector=AuthoritySelector(
                            kind=NODE_KIND_TABLE, property_path=path
                        ),
                        authority=AuthorityTarget(provider_type=provider),
                    ),
                    declarations=(AuthorityDeclaration(f"rule-{provider}"),),
                ),
            )
        )

    saved_boundary = {
        "actions": [
            {
                "action_type": "create",
                "local_id": "tbl:governance-demo/governance_demo/commerce/customers",
                "object_kind": "asset",
                "properties": [
                    {
                        "decision": None,
                        "object": identity.to_dict(),
                        "property": "/description",
                        "roles": ["mutation"],
                    }
                ],
            }
        ],
        "assumptions_schema": "governance-reconciliation-assumptions",
        "assumptions_version": "1",
    }
    with_odcs = recompute_assumptions_on_saved_boundary(
        saved_assumptions=saved_boundary,
        conflict_report=analyze_property_conflicts(observations, _authority("odcs")),
    )
    assert with_odcs["actions"][0]["properties"][0]["decision"]["effective_value"] == (
        "from-odcs"
    )

    _patch_env(monkeypatch)
    _patch_scanner(monkeypatch)
    _write_authority(tmp_path / "auth.yaml", provider_type="dbt")
    config, plan_path, code = _generate_plan(monkeypatch, tmp_path)
    assert code == 0
    capsys.readouterr()

    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["reconciliation_assumptions"] = with_odcs
    payload["reconciliation_assumptions_identity"] = assumptions_content_identity(
        with_odcs
    ).to_dict()
    without = {key: value for key, value in payload.items() if key != "content_identity"}
    payload["content_identity"] = plan_identity(
        without, plan_version=PLAN_VERSION_V2
    ).to_dict()
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    config = _write_workspace(tmp_path, authority_files=["auth.yaml"])
    adapter_calls = _patch_adapter(monkeypatch)

    monkeypatch.setattr(
        "governance.cli._compose_or_empty_reconciliation_bundle",
        lambda **kwargs: ReconciliationSourceBundle(
            observations=observations,
            known_objects=(identity,),
        ),
    )
    code = main(
        [
            "apply",
            str(plan_path),
            "--config",
            str(config),
            "--dbt-manifest",
            str(tmp_path / "unused.json"),
            "--format",
            "json",
        ]
    )
    assert code == 5
    out = json.loads(capsys.readouterr().out)
    assert out["stale"] is True
    assert any(item["category"] == "reconciliation" for item in out["mismatches"])
    assert int(adapter_calls["writes"]) == 0
