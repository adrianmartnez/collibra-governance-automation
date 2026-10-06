"""Conformance tests for example.catalog using the public governance.conformance kit."""

from __future__ import annotations

from pathlib import Path

from governance.conformance import (
    GraphCase,
    ObservationsCase,
    RegistrationCase,
    assert_conformance,
    empty_runtime_context,
    run_provider_conformance,
)
from governance_provider_example.provider import register

SAMPLE_ROOT = Path(__file__).resolve().parents[1] / "sample"


def test_full_provider_conformance() -> None:
    registration = register()
    context = empty_runtime_context(
        {"path": "catalog.json", "namespace": "demo"},
        config_root=str(SAMPLE_ROOT),
    )
    report = run_provider_conformance(
        register=RegistrationCase(register=register),
        scenarios=(
            GraphCase(registration=registration, context=context),
            ObservationsCase(registration=registration, context=context),
        ),
    )
    assert_conformance(report)


def test_entry_point_provider_id() -> None:
    registration = register()
    assert registration.descriptor.provider_id == "example.catalog"
    assert {cap.value for cap in registration.descriptor.capabilities} == {
        "governance_graph",
        "property_observations",
    }
