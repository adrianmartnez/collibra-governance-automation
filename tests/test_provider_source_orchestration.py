"""Provider-driven source orchestration tests (#96)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from governance.config import Settings
from governance.config_contract.provider_resolution import (
    ResolvedProviderBinding,
    construct_provider_capability,
)
from governance.domain import DataSource, GovernanceModel, make_datasource_id
from governance.orchestration.compat_v1 import postgresql_binding_from_settings
from governance.orchestration.registry import build_provider_registry
from governance.orchestration.sources import (
    compose_legacy_impact_jobs,
    run_metadata_discovery,
)
from governance.providers import CapabilityId
from governance.reconciliation.sources import compose_reconciliation_sources

NS = "acme.commerce"


def _settings() -> Settings:
    return Settings(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="governance_demo",
        postgres_user="postgres",
        postgres_password="secret",
        postgres_source_name="governance-demo",
        inventory_output_path="inventory.json",
    )


def test_run_metadata_discovery_via_postgresql_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = GovernanceModel(
        data_sources=(
            DataSource(
                id=make_datasource_id("governance-demo"),
                name="governance-demo",
                system_type="postgresql",
            ),
        )
    )

    class _Scanner:
        def __init__(self, _params: object) -> None:
            pass

        def scan(self) -> GovernanceModel:
            return expected

    monkeypatch.setattr(
        "governance.providers.builtins.postgresql.PostgresMetadataScanner",
        _Scanner,
    )
    registry = build_provider_registry(discover_external=False)
    binding = postgresql_binding_from_settings(_settings(), registry)
    assert run_metadata_discovery(binding) is expected


def test_compose_legacy_impact_jobs_order_dbt_odcs_openlineage() -> None:
    registry = build_provider_registry(discover_external=False)
    jobs = compose_legacy_impact_jobs(
        namespace=NS,
        odcs_paths=["z.odcs.yaml"],
        dbt_paths=["a.json"],
        openlineage_paths=["m.json"],
        dbt_default_database=None,
        registry=registry,
    )
    kinds = [job.logical_id.split("-", 1)[0] for job in jobs]
    assert kinds == ["dbt", "odcs", "openlineage"]


def test_reconciliation_shim_imports_compose_reconciliation_sources() -> None:
    from governance.reconciliation import sources as reconciliation_sources

    assert reconciliation_sources.compose_reconciliation_sources is compose_reconciliation_sources


def test_capability_isolation_duplicate_read_ok(tmp_path: Path) -> None:
    doc = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": "contract-orders",
        "version": "1.0.0",
        "status": "active",
        "name": "Orders Contract",
    }
    path = tmp_path / "c.odcs.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    registry = build_provider_registry(discover_external=False)
    registration = registry.get("odcs")
    binding = ResolvedProviderBinding(
        role="source",
        logical_id="odcs-0",
        provider_id="odcs",
        registration=registration,
        runtime_context=__import__(
            "governance.providers.contracts", fromlist=["ProviderRuntimeContext"]
        ).ProviderRuntimeContext(
            config={"path": str(path), "namespace": NS},
            config_root=None,
        ),
        config_path="/test/odcs",
    )
    graph_cap = construct_provider_capability(binding, CapabilityId.GOVERNANCE_GRAPH)
    obs_cap = construct_provider_capability(binding, CapabilityId.PROPERTY_OBSERVATIONS)
    graph = graph_cap.load_graph()
    observations = obs_cap.load_observations()
    assert graph.nodes
    assert observations.observations is not None
