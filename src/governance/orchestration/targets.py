"""Thin Collibra target capability invocations for legacy CLI paths."""

from __future__ import annotations

from typing import Any, cast

from governance.config_contract.provider_resolution import (
    ResolvedProviderBinding,
    construct_provider_capability,
)
from governance.integrations.collibra.import_api import preview_collibra_plan_result
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import (
    CollibraDesiredState,
    CollibraRemoteState,
    SyncPlan,
)
from governance.integrations.collibra.sync import dry_run_sync_result
from governance.providers import CapabilityId
from governance.providers.builtins.collibra import CollibraMutationRequest
from governance.providers.contracts import (
    AuthorizedMutationCapability,
    CompatibilityPreflightCapability,
    RemoteStateReadCapability,
    TargetPlanningCapability,
)


def invoke_remote_state_read(
    binding: ResolvedProviderBinding,
    desired: CollibraDesiredState,
) -> CollibraRemoteState:
    capability = cast(
        RemoteStateReadCapability[CollibraDesiredState, CollibraRemoteState],
        construct_provider_capability(binding, CapabilityId.REMOTE_STATE_READ),
    )
    return capability.read_remote_state(desired)


def invoke_target_planning(
    binding: ResolvedProviderBinding,
    desired: CollibraDesiredState,
    remote: CollibraRemoteState,
) -> SyncPlan:
    capability = cast(
        TargetPlanningCapability[CollibraDesiredState, CollibraRemoteState, SyncPlan],
        construct_provider_capability(binding, CapabilityId.TARGET_PLANNING),
    )
    return capability.build_plan(desired, remote)


def invoke_preflight(binding: ResolvedProviderBinding) -> Any:
    capability = cast(
        CompatibilityPreflightCapability[Any],
        construct_provider_capability(binding, CapabilityId.COMPATIBILITY_PREFLIGHT),
    )
    return capability.run_preflight()


def invoke_authorized_mutation(
    binding: ResolvedProviderBinding,
    request: CollibraMutationRequest,
) -> Any:
    capability = cast(
        AuthorizedMutationCapability[CollibraMutationRequest, Any],
        construct_provider_capability(binding, CapabilityId.AUTHORIZED_MUTATION),
    )
    return capability.execute_authorized(request)


def dry_run_collibra_plan_result(
    *,
    plan: SyncPlan,
    mapping_config: CollibraMappingConfig,
    execution_mode: str,
    synchronization_id: str | None = None,
    max_resources: int | None = None,
    max_additional_characteristics: int | None = None,
) -> Any:
    """Dry-run execution preview without authorized mutation capability."""
    mode = (execution_mode or "core_rest").strip().lower()
    if mode == "core_rest":
        return dry_run_sync_result(plan)
    return preview_collibra_plan_result(
        plan,
        mapping_config,
        execution_mode=mode,
        synchronization_id=synchronization_id,
        max_resources=max_resources,
        max_additional_characteristics=max_additional_characteristics,
    )


__all__ = [
    "dry_run_collibra_plan_result",
    "invoke_authorized_mutation",
    "invoke_preflight",
    "invoke_remote_state_read",
    "invoke_target_planning",
]
