"""Saved governance plan artifact model."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from governance.exporters.inventory import SCANNER_CONTRACT_VERSION
from governance.identity.hashing import ContentIdentity, plan_identity
from governance.integrations.collibra.models import SyncPlan
from governance.integrations.collibra.sync import PLANNER_CONTRACT_VERSION

PLAN_SCHEMA = "governance-plan"
PLAN_VERSION = "1"
PLAN_VERSION_V2 = "2"


@dataclass(frozen=True, slots=True)
class SavedGovernancePlan:
    sync_plan: SyncPlan
    config_identity: ContentIdentity
    snapshot_identity: ContentIdentity
    policy_identity: ContentIdentity
    mapping_identity: ContentIdentity
    target_context: dict[str, str]
    target_context_identity: ContentIdentity
    remote_state_identity: ContentIdentity
    planner_contract_version: str = PLANNER_CONTRACT_VERSION
    scanner_contract_version: str = SCANNER_CONTRACT_VERSION
    plan_schema: str = PLAN_SCHEMA
    plan_version: str = PLAN_VERSION_V2
    reconciliation_assumptions: dict[str, Any] | None = None
    reconciliation_assumptions_identity: ContentIdentity | None = None

    def __post_init__(self) -> None:
        if self.plan_version != PLAN_VERSION_V2:
            return
        from governance.reconciliation.assumptions import (
            assumptions_content_identity,
            empty_assumptions,
        )

        assumptions = self.reconciliation_assumptions
        identity = self.reconciliation_assumptions_identity
        if assumptions is None:
            assumptions = empty_assumptions()
            object.__setattr__(self, "reconciliation_assumptions", assumptions)
        if identity is None:
            object.__setattr__(
                self,
                "reconciliation_assumptions_identity",
                assumptions_content_identity(assumptions),
            )

    def canonical_dict_without_identity(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "actions": [action.to_dict() for action in self.sync_plan.actions],
            "config_identity": self.config_identity.to_dict(),
            "mapping_identity": self.mapping_identity.to_dict(),
            "plan_schema": self.plan_schema,
            "plan_version": self.plan_version,
            "planner_contract_version": self.planner_contract_version,
            "policy_identity": self.policy_identity.to_dict(),
            "remote_state_identity": self.remote_state_identity.to_dict(),
            "scanner_contract_version": self.scanner_contract_version,
            "snapshot_identity": self.snapshot_identity.to_dict(),
            "target_context": dict(self.target_context),
            "target_context_identity": self.target_context_identity.to_dict(),
        }
        if self.plan_version == PLAN_VERSION_V2:
            if self.reconciliation_assumptions is None:
                raise ValueError("reconciliation_assumptions required for plan_version 2")
            if self.reconciliation_assumptions_identity is None:
                raise ValueError(
                    "reconciliation_assumptions_identity required for plan_version 2"
                )
            payload["reconciliation_assumptions"] = self.reconciliation_assumptions
            payload["reconciliation_assumptions_identity"] = (
                self.reconciliation_assumptions_identity.to_dict()
            )
        return payload

    def content_identity(self) -> ContentIdentity:
        return plan_identity(
            self.canonical_dict_without_identity(),
            plan_version=self.plan_version,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict_without_identity()
        payload["content_identity"] = self.content_identity().to_dict()
        return payload

    def to_json(self) -> str:
        return (
            json.dumps(
                self.to_dict(),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n"
        )
