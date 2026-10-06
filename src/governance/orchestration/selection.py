"""Provider binding selection for v2 orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from governance.config_contract.errors import CODE_SEMANTIC, ConfigSemanticError, DiagnosticError
from governance.config_contract.provider_resolution import (
    ResolvedProviderBinding,
    ResolvedProviderConfiguration,
)
from governance.orchestration.sources import SourceCapabilityJob
from governance.providers import CapabilityId, ProviderRegistry


def select_bindings_with_capability(
    resolved: ResolvedProviderConfiguration,
    *,
    role: Literal["source", "target"],
    capability: CapabilityId,
) -> tuple[ResolvedProviderBinding, ...]:
    bindings = resolved.sources if role == "source" else resolved.targets
    selected = [
        binding
        for binding in bindings
        if capability in binding.registration.descriptor.capabilities
    ]
    return tuple(sorted(selected, key=lambda item: item.logical_id))


def require_unique_binding(
    bindings: Sequence[ResolvedProviderBinding],
    *,
    capability: CapabilityId,
    role: str,
) -> ResolvedProviderBinding:
    cap_label = capability.value if isinstance(capability, CapabilityId) else str(capability)
    role_path = f"/{role}s"
    if not bindings:
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=role_path,
                    message=(
                        f"no {role} provider advertises capability {cap_label!r}; "
                        "add exactly one matching provider binding"
                    ),
                )
            ]
        )
    if len(bindings) > 1:
        ids = ", ".join(sorted(item.logical_id for item in bindings))
        raise ConfigSemanticError(
            [
                DiagnosticError(
                    code=CODE_SEMANTIC,
                    path=role_path,
                    message=(f"ambiguous {role} providers for capability {cap_label!r}: {ids}"),
                )
            ]
        )
    return bindings[0]


def compose_v2_reconciliation_jobs(
    resolved: ResolvedProviderConfiguration,
    registry: ProviderRegistry,
) -> list[SourceCapabilityJob]:
    _ = registry
    bindings = select_bindings_with_capability(
        resolved,
        role="source",
        capability=CapabilityId.PROPERTY_OBSERVATIONS,
    )
    jobs: list[SourceCapabilityJob] = []
    for binding in bindings:
        caps = frozenset(binding.registration.descriptor.capabilities)
        jobs.append(
            SourceCapabilityJob(
                logical_id=binding.logical_id,
                provider_id=binding.provider_id,
                binding=binding,
                capabilities=caps,
                diagnostic_path=binding.config_path,
            )
        )
    return jobs


def compose_v2_impact_jobs(
    resolved: ResolvedProviderConfiguration,
    registry: ProviderRegistry,
) -> list[SourceCapabilityJob]:
    _ = registry
    bindings = select_bindings_with_capability(
        resolved,
        role="source",
        capability=CapabilityId.GOVERNANCE_GRAPH,
    )
    jobs: list[SourceCapabilityJob] = []
    for binding in bindings:
        caps = frozenset(binding.registration.descriptor.capabilities)
        jobs.append(
            SourceCapabilityJob(
                logical_id=binding.logical_id,
                provider_id=binding.provider_id,
                binding=binding,
                capabilities=caps,
                diagnostic_path=binding.config_path,
            )
        )
    return jobs


__all__ = [
    "compose_v2_impact_jobs",
    "compose_v2_reconciliation_jobs",
    "require_unique_binding",
    "select_bindings_with_capability",
]
