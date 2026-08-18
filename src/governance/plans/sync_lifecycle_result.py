"""Additive sync_v2 lifecycle result with explicit remote job handles."""

from __future__ import annotations

from typing import Any

from governance.identity.hashing import ContentIdentity
from governance.integrations.collibra.jobs import (
    JOB_OBSERVATION_FAILURE,
    JobView,
    is_remote_terminal,
    submission_state_as_bool,
)
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
        "terminal": is_remote_terminal(job),
    }


def _batch_lifecycle_records(result: SyncLifecycleResult) -> list[dict[str, Any]]:
    observation_error = (
        result.error if result.error == JOB_OBSERVATION_FAILURE and not result.batch_jobs else None
    )
    if not result.batch_job_ids:
        if result.batch_submission_state == "unknown":
            return [
                {
                    "submission_state": "unknown",
                    "job_id": None,
                    "job_state": None,
                    "job_result": None,
                    "terminal": False,
                }
            ]
        return []
    records: list[dict[str, Any]] = []
    for index, job_id in enumerate(result.batch_job_ids):
        job = result.batch_jobs[index] if index < len(result.batch_jobs) else None
        record: dict[str, Any] = {
            "submission_state": result.batch_submission_state,
            "job_id": job_id,
            "job_state": job.remote_state if job is not None else None,
            "job_result": job.remote_result if job is not None else None,
            "terminal": is_remote_terminal(job),
        }
        if job is None and observation_error is not None:
            record["observation_error"] = observation_error
        records.append(record)
    return records


def _lifecycle_payload_fields(result: SyncLifecycleResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "applied_count": result.applied_count,
        "batch_job_ids": list(result.batch_job_ids),
        "batch_jobs": [_serialize_job(job) for job in result.batch_jobs],
        "batch_lifecycle": _batch_lifecycle_records(result),
        "batch_submission_state": result.batch_submission_state,
        "dry_run": result.dry_run,
        "execution_mode": "sync_v2",
        "finalization_job_id": result.finalization_job_id,
        "finalization_submission_state": result.finalization_submission_state,
        "finalization_submitted": submission_state_as_bool(result.finalization_submission_state),
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


def build_sync_lifecycle_result(
    *,
    result: SyncLifecycleResult,
    plan_content_identity: ContentIdentity,
) -> dict[str, Any]:
    payload = _lifecycle_payload_fields(result)
    payload.update(
        {
            "plan_content_identity": plan_content_identity.to_dict(),
            "result_schema": SYNC_LIFECYCLE_RESULT_SCHEMA,
            "result_version": SYNC_LIFECYCLE_RESULT_VERSION,
            "stale": False,
        }
    )
    return payload


def build_sync_lifecycle_sync_payload(
    *,
    mode: str,
    result: SyncLifecycleResult,
) -> dict[str, Any]:
    payload = _lifecycle_payload_fields(result)
    payload.update(
        {
            "mode": mode,
            "result_schema": SYNC_LIFECYCLE_RESULT_SCHEMA,
            "result_version": SYNC_LIFECYCLE_RESULT_VERSION,
        }
    )
    return payload


def format_sync_lifecycle_result_human(payload: dict[str, Any]) -> str:
    lines = [
        f"stale={str(payload.get('stale', False)).lower()}",
        f"execution_mode={payload['execution_mode']}",
        f"dry_run={str(payload['dry_run']).lower()}",
        f"success={str(payload['success']).lower()}",
        f"synchronization_id={payload['synchronization_id']}",
        f"batch_submission_state={payload['batch_submission_state']}",
        f"finalization_submission_state={payload['finalization_submission_state']}",
        f"applied_count={payload['applied_count']}",
    ]
    finalize_id = payload.get("finalization_job_id")
    if finalize_id is not None:
        lines.append(f"finalization_job_id={finalize_id}")
    if payload.get("finalization_state") is not None:
        lines.append(f"finalization_state={payload['finalization_state']}")
    if payload.get("finalization_result") is not None:
        lines.append(f"finalization_result={payload['finalization_result']}")
    batch_job_ids = payload.get("batch_job_ids") or []
    for index, job_id in enumerate(batch_job_ids):
        if job_id:
            lines.append(f"batch_job_id_{index}={job_id}")
    summary = payload.get("import_error_summary")
    if isinstance(summary, dict) and "error_count" in summary:
        lines.append(f"error_count={summary['error_count']}")
    if payload.get("error"):
        lines.append(f"error={payload['error']}")
    return "\n".join(lines) + "\n"
