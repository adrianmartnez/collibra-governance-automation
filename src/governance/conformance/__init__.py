"""Public Provider SDK conformance test kit.

Installed providers are trusted Python dependencies. This kit validates
cooperative observable behavior; it is not a security sandbox, malicious-code
audit, vendor certification, or production certification.

Partial suite helpers (``run_source_capability_conformance``,
``run_target_capability_conformance``) may exercise a deliberate subset of
capabilities. Full provider conformance via ``run_provider_conformance``
requires a scenario for **every** declared capability.
"""

from __future__ import annotations

from governance.conformance.cases import (
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
    empty_runtime_context,
)
from governance.conformance.registration import (
    run_descriptor_conformance,
    run_registration_conformance,
)
from governance.conformance.report import (
    ConformanceCheckResult,
    ConformanceFailure,
    ConformanceReport,
    assert_conformance,
)
from governance.conformance.runner import run_provider_conformance
from governance.conformance.secrets import run_secret_safety_conformance
from governance.conformance.source import (
    run_graph_conformance,
    run_lineage_conformance,
    run_metadata_discovery_conformance,
    run_observations_conformance,
    run_source_capability_conformance,
)
from governance.conformance.target import (
    run_mutation_conformance,
    run_planning_conformance,
    run_preflight_conformance,
    run_remote_read_conformance,
    run_target_capability_conformance,
)

__all__ = [
    "ConformanceCheckResult",
    "ConformanceFailure",
    "ConformanceReport",
    "DescriptorCase",
    "GraphCase",
    "LineageCase",
    "MetadataDiscoveryCase",
    "MutationCase",
    "ObservationsCase",
    "PlanningCase",
    "PreflightCase",
    "RegistrationCase",
    "RemoteReadCase",
    "SecretSafetyCase",
    "assert_conformance",
    "empty_runtime_context",
    "run_descriptor_conformance",
    "run_graph_conformance",
    "run_lineage_conformance",
    "run_metadata_discovery_conformance",
    "run_mutation_conformance",
    "run_observations_conformance",
    "run_planning_conformance",
    "run_preflight_conformance",
    "run_provider_conformance",
    "run_registration_conformance",
    "run_remote_read_conformance",
    "run_secret_safety_conformance",
    "run_source_capability_conformance",
    "run_target_capability_conformance",
]
