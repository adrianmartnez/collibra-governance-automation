"""Apply result contract for fresh (non-stale) apply paths."""

from __future__ import annotations

from typing import Any

from governance.identity.hashing import ContentIdentity
from governance.integrations.collibra.models import SyncPlan, SyncResult
from governance.plans.errors import APPLY_RESULT_SCHEMA, APPLY_RESULT_VERSION


def build_apply_result(
    *,
    sync_plan: SyncPlan,
    result: SyncResult,
    plan_content_identity: ContentIdentity,
) -> dict[str, Any]:
    return {
        "action_counts": {
            "create": len(sync_plan.creates),
            "remote_only": len(sync_plan.remote_only),
            "unchanged": len(sync_plan.unchanged),
            "update": len(sync_plan.updates),
        },
        "applied_count": result.applied_count,
        "dry_run": result.dry_run,
        "error": result.error,
        "failed_action": (
            result.failed_action.to_dict() if result.failed_action is not None else None
        ),
        "plan_content_identity": plan_content_identity.to_dict(),
        "result_schema": APPLY_RESULT_SCHEMA,
        "result_version": APPLY_RESULT_VERSION,
        "stale": False,
        "success": result.success,
        "unchanged_count": result.unchanged_count,
    }


def format_apply_result_human(payload: dict[str, Any]) -> str:
    lines = [
        f"stale={str(payload['stale']).lower()}",
        f"dry_run={str(payload['dry_run']).lower()}",
        f"success={str(payload['success']).lower()}",
        f"applied_count={payload['applied_count']}",
        f"unchanged_count={payload['unchanged_count']}",
        (
            "action_counts "
            f"create={payload['action_counts']['create']} "
            f"update={payload['action_counts']['update']} "
            f"unchanged={payload['action_counts']['unchanged']} "
            f"remote_only={payload['action_counts']['remote_only']}"
        ),
    ]
    if payload.get("error"):
        lines.append(f"error={payload['error']}")
    return "\n".join(lines) + "\n"
