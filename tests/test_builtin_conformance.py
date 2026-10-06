"""Built-in providers exercise the public conformance kit (unit/fixture)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import yaml

from governance.conformance import (
    GraphCase,
    LineageCase,
    MutationCase,
    ObservationsCase,
    PlanningCase,
    PreflightCase,
    RegistrationCase,
    RemoteReadCase,
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
_TESTS_DIR = Path(__file__).resolve().parent


def _load_test_module(filename: str, module_name: str) -> ModuleType:
    path = _TESTS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_builtin_helpers = _load_test_module("test_builtin_providers.py", "_gac_builtin_helpers")
_dbt_helpers = _load_test_module("test_dbt_manifest_ingestion.py", "_gac_dbt_helpers")
_ol_helpers = _load_test_module("test_openlineage_ingestion.py", "_gac_ol_helpers")
_collibra_helpers = _load_test_module("test_collibra_mock_adapter.py", "_gac_collibra_helpers")


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
    doc_path.write_text(
        yaml.safe_dump(_builtin_helpers._minimal_odcs_doc(), sort_keys=False),
        encoding="utf-8",
    )
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
    manifest = _dbt_helpers._minimal_v12_manifest(nodes={"model.pkg.orders": _dbt_helpers._model()})
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
    in_facets = {
        "hierarchy": _ol_helpers._physical_hierarchy_facet("analytics", "raw", "customers")
    }
    out_facets = {
        "hierarchy": _ol_helpers._physical_hierarchy_facet("analytics", "marts", "orders"),
        "columnLineage": _ol_helpers._column_lineage_facet(
            {"c": {"inputFields": [_ol_helpers._input_field(_ol_helpers.OL_NS, "in_tbl", "a")]}},
        ),
    }
    event = _ol_helpers._run_event(
        inputs=[_ol_helpers._dataset(namespace=_ol_helpers.OL_NS, name="in_tbl", facets=in_facets)],
        outputs=[
            _ol_helpers._dataset(namespace=_ol_helpers.OL_NS, name="out_tbl", facets=out_facets)
        ],
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
    mapping = mock_mapping_config()
    registration = register_collibra()
    context = empty_runtime_context(
        {
            "mode": "mock",
            "mapping": mapping.to_identity_dict(),
        }
    )
    model = _collibra_helpers._tiny_model()
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
    incomplete = run_provider_conformance(register=RegistrationCase(register=register_postgresql))
    assert not incomplete.passed
    assert any(item.id == "missing_conformance_scenario" for item in incomplete.failures)
