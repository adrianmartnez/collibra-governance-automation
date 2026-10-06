"""Full and orchestrated provider conformance entry points."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from governance.conformance.cases import (
    CapabilityScenario,
    DescriptorCase,
    GraphCase,
    LineageCase,
    MetadataDiscoveryCase,
    MutationCase,
    ObservationsCase,
    PlanningCase,
    PreflightCase,
    RegistrationCase,
    RemoteReadCase,
    SecretSafetyCase,
    scenario_capability_id,
)
from governance.conformance.registration import (
    run_descriptor_conformance,
    run_registration_conformance,
)
from governance.conformance.report import (
    ConformanceCheckResult,
    ConformanceReport,
    check,
    merge_reports,
)
from governance.conformance.secrets import run_secret_safety_conformance
from governance.conformance.source import (
    run_graph_conformance,
    run_lineage_conformance,
    run_metadata_discovery_conformance,
    run_observations_conformance,
)
from governance.conformance.target import (
    run_mutation_conformance,
    run_planning_conformance,
    run_preflight_conformance,
    run_remote_read_conformance,
)
from governance.providers.contracts import ProviderRegistration


def _run_capability_scenario(scenario: CapabilityScenario) -> ConformanceReport:
    if isinstance(scenario, MetadataDiscoveryCase):
        return run_metadata_discovery_conformance(scenario)
    if isinstance(scenario, GraphCase):
        return run_graph_conformance(scenario)
    if isinstance(scenario, ObservationsCase):
        return run_observations_conformance(scenario)
    if isinstance(scenario, LineageCase):
        return run_lineage_conformance(scenario)
    if isinstance(scenario, RemoteReadCase):
        return run_remote_read_conformance(scenario)
    if isinstance(scenario, PlanningCase):
        return run_planning_conformance(scenario)
    if isinstance(scenario, PreflightCase):
        return run_preflight_conformance(scenario)
    if isinstance(scenario, MutationCase):
        return run_mutation_conformance(scenario)
    raise TypeError(f"unsupported capability scenario: {type(scenario)!r}")


def _index_scenarios(
    scenarios: Sequence[CapabilityScenario],
) -> tuple[Mapping[str, CapabilityScenario], tuple[ConformanceCheckResult, ...]]:
    indexed: dict[str, CapabilityScenario] = {}
    diagnostics: list[ConformanceCheckResult] = []
    for scenario in scenarios:
        cap = scenario_capability_id(scenario)
        if cap in indexed:
            diagnostics.append(
                check(
                    id="duplicate_conformance_scenario",
                    passed=False,
                    message=f"duplicate capability scenario for {cap!r}",
                    path=f"/scenarios/{cap}",
                )
            )
            continue
        indexed[cap] = scenario
    return indexed, tuple(diagnostics)


def run_provider_conformance(
    *,
    register: RegistrationCase | None = None,
    registration: ProviderRegistration | None = None,
    scenarios: Sequence[CapabilityScenario] = (),
    secret_safety: SecretSafetyCase | None = None,
) -> ConformanceReport:
    """Full provider conformance.

    Requires a compatible scenario for **every** capability declared by the
    provider. Missing scenarios fail with ``missing_conformance_scenario``.

    Partial suite helpers (``run_source_capability_conformance`` /
    ``run_target_capability_conformance``) deliberately allow subsets and are
    **not** equivalent to full provider conformance.
    """
    reports: list[ConformanceReport] = []

    resolved: ProviderRegistration | None = registration
    if register is not None:
        reg_report = run_registration_conformance(register)
        reports.append(reg_report)
        if resolved is None:
            try:
                resolved = register.register()
            except Exception:  # noqa: BLE001
                resolved = None

    if resolved is None:
        reports.append(
            ConformanceReport(
                results=(
                    check(
                        id="provider_registration_required",
                        passed=False,
                        message=(
                            "full conformance requires RegistrationCase and/or "
                            "an explicit ProviderRegistration"
                        ),
                        path="/registration",
                    ),
                )
            )
        )
        return merge_reports(*reports) if reports else ConformanceReport(results=())

    if register is None:
        reports.append(run_descriptor_conformance(DescriptorCase(registration=resolved)))

    if secret_safety is not None:
        reports.append(run_secret_safety_conformance(secret_safety))

    indexed, index_diagnostics = _index_scenarios(scenarios)
    if index_diagnostics:
        reports.append(ConformanceReport(results=index_diagnostics))

    missing: list[ConformanceCheckResult] = []
    for capability in resolved.descriptor.capabilities:
        cap = capability.value
        if cap not in indexed:
            missing.append(
                check(
                    id="missing_conformance_scenario",
                    passed=False,
                    message=(
                        f"declared capability {cap!r} has no conformance scenario; "
                        "full conformance requires a scenario for every declared capability"
                    ),
                    path=f"/scenarios/{cap}",
                )
            )
    if missing:
        reports.append(ConformanceReport(results=tuple(missing)))

    for capability in resolved.descriptor.capabilities:
        cap = capability.value
        scenario = indexed.get(cap)
        if scenario is None:
            continue
        # Scenario registration should match the provider under test when both given.
        if scenario.registration.descriptor.provider_id != resolved.descriptor.provider_id:
            reports.append(
                ConformanceReport(
                    results=(
                        check(
                            id="scenario_provider_mismatch",
                            passed=False,
                            message=(
                                f"scenario for {cap!r} targets provider "
                                f"{scenario.registration.descriptor.provider_id!r}, "
                                f"expected {resolved.descriptor.provider_id!r}"
                            ),
                            path=f"/scenarios/{cap}",
                        ),
                    )
                )
            )
            continue
        reports.append(_run_capability_scenario(scenario))

    # Extra scenarios for undeclared capabilities are reported (not silent).
    declared = {cap.value for cap in resolved.descriptor.capabilities}
    extras = [
        check(
            id="undeclared_conformance_scenario",
            passed=False,
            message=f"scenario provided for undeclared capability {cap!r}",
            path=f"/scenarios/{cap}",
        )
        for cap in sorted(indexed)
        if cap not in declared
    ]
    if extras:
        reports.append(ConformanceReport(results=tuple(extras)))

    if not reports:
        return ConformanceReport(results=())
    return merge_reports(*reports)


__all__ = [
    "run_provider_conformance",
]
