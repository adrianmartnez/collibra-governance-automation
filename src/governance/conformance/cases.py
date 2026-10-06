"""Public scenario/case types for Provider SDK conformance."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from governance.providers.contracts import ProviderRegistration, ProviderRuntimeContext


@dataclass(frozen=True, slots=True)
class RegistrationCase:
    """Exercise the zero-argument registration callable twice."""

    register: Callable[[], ProviderRegistration]

    def __post_init__(self) -> None:
        if not callable(self.register):
            raise TypeError("RegistrationCase.register must be callable")


@dataclass(frozen=True, slots=True)
class DescriptorCase:
    """Validate an already-built ProviderRegistration descriptor surface."""

    registration: ProviderRegistration


@dataclass(frozen=True, slots=True)
class SecretSafetyCase:
    """Observable secret-safety checks against controlled markers."""

    registration: ProviderRegistration
    context: ProviderRuntimeContext
    forbidden_substrings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetadataDiscoveryCase:
    registration: ProviderRegistration
    context: ProviderRuntimeContext
    project: Callable[[Any], Any] | None = None
    mutation_counter: Callable[[], int] | None = None


@dataclass(frozen=True, slots=True)
class GraphCase:
    registration: ProviderRegistration
    context: ProviderRuntimeContext


@dataclass(frozen=True, slots=True)
class ObservationsCase:
    registration: ProviderRegistration
    context: ProviderRuntimeContext


@dataclass(frozen=True, slots=True)
class LineageCase:
    registration: ProviderRegistration
    context: ProviderRuntimeContext


@dataclass(frozen=True, slots=True)
class RemoteReadCase:
    registration: ProviderRegistration
    context: ProviderRuntimeContext
    request: Any
    expect_type: type | tuple[type, ...] | None = None
    project: Callable[[Any], Any] | None = None
    mutation_counter: Callable[[], int] | None = None


@dataclass(frozen=True, slots=True)
class PlanningCase:
    registration: ProviderRegistration
    context: ProviderRuntimeContext
    desired: Any
    remote: Any
    project: Callable[[Any], Any] | None = None
    pure_planning: bool = False
    mutation_counter: Callable[[], int] | None = None
    io_counter: Callable[[], int] | None = None


@dataclass(frozen=True, slots=True)
class PreflightCase:
    registration: ProviderRegistration
    context: ProviderRuntimeContext
    project: Callable[[Any], Any] | None = None
    mutation_counter: Callable[[], int] | None = None


@dataclass(frozen=True, slots=True)
class MutationCase:
    """Explicitly authorized mutation harness — does not grant authorization."""

    registration: ProviderRegistration
    context: ProviderRuntimeContext
    request: Any
    project: Callable[[Any], Any] | None = None
    authorized: bool = False


CapabilityScenario = (
    MetadataDiscoveryCase
    | GraphCase
    | ObservationsCase
    | LineageCase
    | RemoteReadCase
    | PlanningCase
    | PreflightCase
    | MutationCase
)


def scenario_capability_id(scenario: CapabilityScenario) -> str:
    if isinstance(scenario, MetadataDiscoveryCase):
        return "metadata_discovery"
    if isinstance(scenario, GraphCase):
        return "governance_graph"
    if isinstance(scenario, ObservationsCase):
        return "property_observations"
    if isinstance(scenario, LineageCase):
        return "lineage"
    if isinstance(scenario, RemoteReadCase):
        return "remote_state_read"
    if isinstance(scenario, PlanningCase):
        return "target_planning"
    if isinstance(scenario, PreflightCase):
        return "compatibility_preflight"
    if isinstance(scenario, MutationCase):
        return "authorized_mutation"
    raise TypeError(f"unsupported capability scenario type: {type(scenario)!r}")


def scenario_registration(scenario: CapabilityScenario) -> ProviderRegistration:
    return scenario.registration


def empty_runtime_context(
    config: Mapping[str, object] | None = None,
    *,
    config_root: str | None = None,
) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(config=dict(config or {}), config_root=config_root)


__all__ = [
    "CapabilityScenario",
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
    "empty_runtime_context",
    "scenario_capability_id",
    "scenario_registration",
]
