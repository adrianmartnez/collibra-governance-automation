"""Built-in providers exercise the public conformance kit (unit/fixture)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from tests.test_builtin_providers import _minimal_odcs_doc
from tests.test_collibra_mock_adapter import _tiny_model
from tests.test_dbt_manifest_ingestion import _minimal_v12_manifest, _model

from governance.conformance import (
    GraphCase,
    LineageCase,
    ObservationsCase,
    RegistrationCase,
    assert_conformance,
    empty_runtime_context,
    run_provider_conformance,
    run_registration_conformance,
)
from governance.integrations.collibra import (
    map_to_desired_state,
    mock_mapping_config,
)
from governance.integrations.collibra.sync import build_sync_plan
from governance.providers.builtins.collibra import CollibraMutationRequest
from governance.providers.builtins.collibra import register as register_collibra
from governance.providers.builtins.dbt import register as register_dbt
from governance.providers.builtins.odcs import register as register_odcs
from governance.providers.builtins.openlineage import register as register_openlineage
from governance.providers.builtins.postgresql import register as register_postgresql

NS = "conformance.demo"


def test_builtin_registration_conformance() -> None:
    for register in (
        register_postgresql,
        register_odcs,
        register_dbt,
        register_openlineage,
        register_collibra,
    ):
        report = run_registration_conformance(RegistrationCase(register=register))
        assert_conformance(report)


def test_odcs_full_conformance(tmp_path: Path) -> None:
    doc_path = tmp_path / "contract.odcs.yaml"
    doc_path.write_text(yaml.safe_dump(_minimal_odcs_doc(), sort_keys=False), encoding="utf-8")
    registration = register_odcs()
    context = empty_runtime_context(
        {"path": doc_path.name, "namespace": NS},
        config_root=str(tmp_path),
    )
    report = run_provider_conformance(
        register=RegistrationCase(register=register_odcs),
        scenarios=(
            GraphCase(registration=registration, context=context),
            ObservationsCase(registration=registration, context=context),
        ),
    )
    assert_conformance(report)


def test_dbt_full_conformance(tmp_path: Path) -> None:
    manifest = _minimal_v12_manifest(nodes={"model.pkg.orders": _model()})
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    registration = register_dbt()
    context = empty_runtime_context(
        {"path": path.name, "namespace": NS},
        config_root=str(tmp_path),
    )
    report = run_provider_conformance(
        register=RegistrationCase(register=register_dbt),
        scenarios=(
            GraphCase(registration=registration, context=context),
            ObservationsCase(registration=registration, context=context),
        ),
    )
    assert_conformance(report)


def test_openlineage_full_conformance(tmp_path: Path) -> None:
    from tests.test_openlineage_ingestion import (
        COLUMN_LINEAGE_URL,
        OL_NS,
        PRODUCER,
        _column_lineage_facet,
        _dataset,
        _input_field,
        _physical_hierarchy_facet,
        _run_event,
    )

    in_facets = {"hierarchy": _physical_hierarchy_facet("analytics", "raw", "customers")}
    out_facets = {
        "hierarchy": _physical_hierarchy_facet("analytics", "marts", "orders"),
        "columnLineage": _column_lineage_facet(
            {"c": {"inputFields": [_input_field(OL_NS, "in_tbl", "a")]}},
            schema_url=COLUMN_LINEAGE_URL,
        ),
    }
    event = _run_event(
        producer=PRODUCER,
        inputs=[_dataset(namespace=OL_NS, name="in_tbl", facets=in_facets)],
        outputs=[_dataset(namespace=OL_NS, name="out_tbl", facets=out_facets)],
    )
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    registration = register_openlineage()
    context = empty_runtime_context(
        {"path": path.name, "namespace": NS},
        config_root=str(tmp_path),
    )
    report = run_provider_conformance(
        register=RegistrationCase(register=register_openlineage),
        scenarios=(
            GraphCase(registration=registration, context=context),
            ObservationsCase(registration=registration, context=context),
            LineageCase(registration=registration, context=context),
        ),
    )
    assert_conformance(report)


def test_collibra_full_conformance_mock() -> None:
    from governance.conformance import (
        MutationCase,
        PlanningCase,
        PreflightCase,
        RemoteReadCase,
    )

    mapping = mock_mapping_config()
    registration = register_collibra()
    context = empty_runtime_context(
        {
            "mode": "mock",
            "mapping": mapping.to_identity_dict(),
        }
    )
    model = _tiny_model()
    desired = map_to_desired_state(model, mapping)
    remote_cap = registration.binding_for("remote_state_read").factory(context)
    remote = remote_cap.read_remote_state(desired)
    plan = build_sync_plan(desired, remote)

    report = run_provider_conformance(
        register=RegistrationCase(register=register_collibra),
        scenarios=(
            RemoteReadCase(
                registration=registration,
                context=context,
                request=desired,
                project=lambda state: (
                    len(state.assets),
                    len(state.relationships),
                    state.unmanaged_assets_ignored,
                ),
            ),
            PlanningCase(
                registration=registration,
                context=context,
                desired=desired,
                remote=remote,
                project=lambda sync_plan: tuple(sorted(sync_plan.to_dict().keys())),
                pure_planning=True,
            ),
            PreflightCase(
                registration=registration,
                context=context,
                project=lambda result: (
                    getattr(result, "ok", None),
                    type(result).__name__,
                    tuple(sorted(result.to_dict().keys())) if hasattr(result, "to_dict") else (),
                ),
            ),
            MutationCase(
                registration=registration,
                context=context,
                request=CollibraMutationRequest(plan=plan),
                authorized=True,
                project=lambda result: type(result).__name__,
            ),
        ),
    )
    assert_conformance(report)


def test_postgresql_registration_only_in_unit() -> None:
    """Operational metadata_discovery belongs in Docker/integration, not unit."""
    report = run_registration_conformance(RegistrationCase(register=register_postgresql))
    assert_conformance(report)
    # Full conformance without scenario must fail completeness for metadata_discovery.
    incomplete = run_provider_conformance(register=RegistrationCase(register=register_postgresql))
    assert not incomplete.passed
    assert any(item.id == "missing_conformance_scenario" for item in incomplete.failures)
