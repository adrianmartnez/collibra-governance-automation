"""Additive sync_v2 lifecycle result with explicit remote job handles."""

from __future__ import annotations

from typing import Any

from governance.identity.hashing import ContentIdentity
from governance.integrations.collibra.jobs import JobView
from governance.integrations.collibra.synchronization import SyncLifecycleResult

SYNC_LIFECYCLE_RESULT_SCHEMA = "governance-sync-lifecycle-result"
SYNC_LIFECYCLE_RESULT_VERSION = "1"


def _serialize_job(job: JobView | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "job_state": job.remote_state,
        "job_result": job.remote_result,
        "normalized_outcome": job.normalized_outcome,
        "normalized_state": job.normalized_state,
    }


def build_sync_lifecycle_result(
    *,
    result: SyncLifecycleResult,
    plan_content_identity: ContentIdentity,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "applied_count": result.applied_count,
        "batch_jobs": [_serialize_job(job) for job in result.batch_jobs],
        "dry_run": result.dry_run,
        "execution_mode": "sync_v2",
        "finalization_job_id": result.finalization_job_id,
        "finalization_submitted": result.finalization_submitted,
        "plan_content_identity": plan_content_identity.to_dict(),
        "result_schema": SYNC_LIFECYCLE_RESULT_SCHEMA,
        "result_version": SYNC_LIFECYCLE_RESULT_VERSION,
        "stale": False,
        "success": result.success,
        "synchronization_id": result.synchronization_id,
    }
    finalize = result.finalization_job
    if finalize is not None:
        payload["finalization_state"] = finalize.remote_state
        payload["finalization_result"] = finalize.remote_result
    if result.import_error_summary is not None:
        payload["import_error_summary"] = dict(result.import_error_summary)
    if result.error:
        payload["error"] = result.error
    return payload


def build_sync_lifecycle_sync_payload(
    *,
    mode: str,
    result: SyncLifecycleResult,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "applied_count": result.applied_count,
        "batch_jobs": [_serialize_job(job) for job in result.batch_jobs],
        "dry_run": result.dry_run,
        "execution_mode": "sync_v2",
        "finalization_job_id": result.finalization_job_id,
        "finalization_submitted": result.finalization_submitted,
        "mode": mode,
        "result_schema": SYNC_LIFECYCLE_RESULT_SCHEMA,
        "result_version": SYNC_LIFECYCLE_RESULT_VERSION,
        "success": result.success,
        "synchronization_id": result.synchronization_id,
    }
    finalize = result.finalization_job
    if finalize is not None:
        payload["finalization_state"] = finalize.remote_state
        payload["finalization_result"] = finalize.remote_result
    if result.import_error_summary is not None:
        payload["import_error_summary"] = dict(result.import_error_summary)
    if result.error:
        payload["error"] = result.error
    return payload


def format_sync_lifecycle_result_human(payload: dict[str, Any]) -> str:
    lines = [
        f"stale={str(payload.get('stale', False)).lower()}",
        f"execution_mode={payload['execution_mode']}",
        f"dry_run={str(payload['dry_run']).lower()}",
        f"success={str(payload['success']).lower()}",
        f"synchronization_id={payload['synchronization_id']}",
        f"finalization_submitted={str(payload['finalization_submitted']).lower()}",
        f"applied_count={payload['applied_count']}",
    ]
    finalize_id = payload.get("finalization_job_id")
    if finalize_id is not None:
        lines.append(f"finalization_job_id={finalize_id}")
    if payload.get("finalization_state") is not None:
        lines.append(f"finalization_state={payload['finalization_state']}")
    if payload.get("finalization_result") is not None:
        lines.append(f"finalization_result={payload['finalization_result']}")
    batch_jobs = payload.get("batch_jobs") or []
    for index, job in enumerate(batch_jobs):
        if isinstance(job, dict) and job.get("job_id"):
            lines.append(f"batch_job_id_{index}={job['job_id']}")
    summary = payload.get("import_error_summary")
    if isinstance(summary, dict) and "error_count" in summary:
        lines.append(f"error_count={summary['error_count']}")
    if payload.get("error"):
        lines.append(f"error={payload['error']}")
    return "\n".join(lines) + "\n"
