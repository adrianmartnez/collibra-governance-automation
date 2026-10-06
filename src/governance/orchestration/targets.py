"""Thin Collibra target capability invocations for legacy CLI paths."""

from __future__ import annotations

from typing import Any

from governance.config_contract.provider_resolution import (
    ResolvedProviderBinding,
    construct_provider_capability,
)
from governance.integrations.collibra.models import (
    CollibraDesiredState,
    CollibraRemoteState,
    SyncPlan,
)
from governance.providers import CapabilityId
from governance.providers.builtins.collibra import CollibraMutationRequest


def invoke_remote_state_read(
    binding: ResolvedProviderBinding,
    desired: CollibraDesiredState,
) -> CollibraRemoteState:
    capability = construct_provider_capability(binding, CapabilityId.REMOTE_STATE_READ)
    read_remote_state = getattr(capability, "read_remote_state", None)
    if not callable(read_remote_state):
        raise TypeError("remote_state_read capability missing read_remote_state()")
    return read_remote_state(desired)


def invoke_target_planning(
    binding: ResolvedProviderBinding,
    desired: CollibraDesiredState,
    remote: CollibraRemoteState,
) -> SyncPlan:
    capability = construct_provider_capability(binding, CapabilityId.TARGET_PLANNING)
    build_plan = getattr(capability, "build_plan", None)
    if not callable(build_plan):
        raise TypeError("target_planning capability missing build_plan()")
    return build_plan(desired, remote)


def invoke_preflight(binding: ResolvedProviderBinding) -> Any:
    capability = construct_provider_capability(binding, CapabilityId.COMPATIBILITY_PREFLIGHT)
    run_preflight = getattr(capability, "run_preflight", None)
    if not callable(run_preflight):
        raise TypeError("compatibility_preflight capability missing run_preflight()")
    return run_preflight()


def invoke_authorized_mutation(
    binding: ResolvedProviderBinding,
    request: CollibraMutationRequest,
) -> Any:
    capability = construct_provider_capability(binding, CapabilityId.AUTHORIZED_MUTATION)
    execute_authorized = getattr(capability, "execute_authorized", None)
    if not callable(execute_authorized):
        raise TypeError("authorized_mutation capability missing execute_authorized()")
    return execute_authorized(request)


__all__ = [
    "invoke_authorized_mutation",
    "invoke_preflight",
    "invoke_remote_state_read",
    "invoke_target_planning",
]
