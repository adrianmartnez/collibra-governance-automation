"""Load and integrity-check saved .gplan artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from governance.identity.hashing import ContentIdentity, plan_identity
from governance.integrations.collibra.models import SyncPlan
from governance.plans.errors import (
    CODE_IDENTITY,
    CODE_MALFORMED_ACTION,
    CODE_PARSE,
    CODE_UNSUPPORTED_ACTION,
    PlanDiagnosticError,
    PlanIntegrityError,
    PlanParseError,
    PlanSchemaError,
)
from governance.plans.models import SavedGovernancePlan
from governance.plans.schema import validate_plan_structure


def load_saved_plan(path: str | Path) -> SavedGovernancePlan:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanParseError(
            [
                PlanDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="unable to read saved plan file",
                )
            ]
        ) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanParseError(
            [
                PlanDiagnosticError(
                    code=CODE_PARSE,
                    path="",
                    message="invalid JSON in saved plan file",
                )
            ]
        ) from exc

    validate_plan_structure(document)
    return _from_validated_document(document)


def _from_validated_document(document: dict[str, Any]) -> SavedGovernancePlan:
    stored_identity = document.get("content_identity")
    without = {key: value for key, value in document.items() if key != "content_identity"}
    recomputed = plan_identity(without)
    if not isinstance(stored_identity, dict) or recomputed.to_dict() != {
        "algorithm": stored_identity.get("algorithm"),
        "digest": stored_identity.get("digest"),
        "hashing_contract_version": stored_identity.get("hashing_contract_version"),
    }:
        raise PlanIntegrityError(
            [
                PlanDiagnosticError(
                    code=CODE_IDENTITY,
                    path="/content_identity",
                    message="saved plan content identity mismatch",
                )
            ]
        )

    try:
        sync_plan = SyncPlan.from_dict({"actions": document["actions"]})
    except ValueError as exc:
        message = str(exc)
        code = (
            CODE_UNSUPPORTED_ACTION
            if "unsupported" in message.lower() or "DELETE" in message
            else CODE_MALFORMED_ACTION
        )
        raise PlanSchemaError(
            [
                PlanDiagnosticError(
                    code=code,
                    path="/actions",
                    message="saved plan actions are invalid",
                )
            ]
        ) from exc

    return SavedGovernancePlan(
        sync_plan=sync_plan,
        config_identity=_identity(document["config_identity"]),
        snapshot_identity=_identity(document["snapshot_identity"]),
        policy_identity=_identity(document["policy_identity"]),
        mapping_identity=_identity(document["mapping_identity"]),
        target_context={
            "mode": str(document["target_context"]["mode"]),
            "provider": str(document["target_context"]["provider"]),
        },
        target_context_identity=_identity(document["target_context_identity"]),
        remote_state_identity=_identity(document["remote_state_identity"]),
        planner_contract_version=str(document["planner_contract_version"]),
        scanner_contract_version=str(document["scanner_contract_version"]),
        plan_schema=str(document["plan_schema"]),
        plan_version=str(document["plan_version"]),
    )


def _identity(payload: Any) -> ContentIdentity:
    if not isinstance(payload, dict):
        raise PlanSchemaError(
            [
                PlanDiagnosticError(
                    code=CODE_MALFORMED_ACTION,
                    path="",
                    message="identity payload must be a mapping",
                )
            ]
        )
    return ContentIdentity(
        algorithm=str(payload["algorithm"]),
        hashing_contract_version=str(payload["hashing_contract_version"]),
        digest=str(payload["digest"]),
    )
