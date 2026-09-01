"""Build and atomically persist saved governance plans."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governance.config import Settings
from governance.exporters.inventory import SCANNER_CONTRACT_VERSION
from governance.identity.hashing import (
    ContentIdentity,
    mapping_identity,
    policy_identity,
    remote_state_identity,
    target_context_identity,
)
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import SyncPlan
from governance.integrations.collibra.sync import PLANNER_CONTRACT_VERSION
from governance.io.atomic import atomic_write_text
from governance.plans.models import PLAN_VERSION_V2, SavedGovernancePlan
from governance.plans.remote_identity import remote_state_identity_projection
from governance.plans.target_context import (
    build_target_context_projection,
    target_context_public,
)
from governance.policy.models import NormalizedPolicySet
from governance.reconciliation.assumptions import (
    assumptions_content_identity,
    empty_assumptions,
)
from governance.snapshots.models import GovernanceSnapshot


def build_saved_plan(
    *,
    settings: Settings,
    sync_plan: SyncPlan,
    config_identity: ContentIdentity,
    snapshot: GovernanceSnapshot,
    policy_set: NormalizedPolicySet,
    mapping_config: CollibraMappingConfig,
    remote_state_identity_value: ContentIdentity,
    reconciliation_assumptions: dict[str, Any] | None = None,
) -> SavedGovernancePlan:
    projection = build_target_context_projection(settings)
    assumptions = (
        empty_assumptions() if reconciliation_assumptions is None else reconciliation_assumptions
    )
    assumptions_identity = assumptions_content_identity(assumptions)
    return SavedGovernancePlan(
        sync_plan=sync_plan,
        config_identity=config_identity,
        snapshot_identity=snapshot.content_identity(),
        policy_identity=policy_identity(policy_set.to_identity_dict()),
        mapping_identity=mapping_identity(mapping_config.to_identity_dict()),
        target_context=target_context_public(projection),
        target_context_identity=target_context_identity(projection),
        remote_state_identity=remote_state_identity_value,
        planner_contract_version=PLANNER_CONTRACT_VERSION,
        scanner_contract_version=SCANNER_CONTRACT_VERSION,
        plan_version=PLAN_VERSION_V2,
        reconciliation_assumptions=assumptions,
        reconciliation_assumptions_identity=assumptions_identity,
    )


def write_saved_plan(plan: SavedGovernancePlan, path: str | Path) -> Path:
    return atomic_write_text(Path(path), plan.to_json())


def compute_remote_state_identity_value(remote) -> ContentIdentity:
    return remote_state_identity(remote_state_identity_projection(remote))
