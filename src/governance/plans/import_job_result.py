"""Additive import_v2 job lifecycle result. Distinct from submission and apply-result v1."""

from __future__ import annotations

from typing import Any

from governance.identity.hashing import ContentIdentity
from governance.integrations.collibra.import_api import ImportJobExecutionResult
from governance.integrations.collibra.jobs import (
    JobView,
    is_remote_terminal,
    submission_state_as_bool,
)

IMPORT_JOB_RESULT_SCHEMA = "governance-import-job-result"
IMPORT_JOB_RESULT_VERSION = "1"


def _job_fields(job: JobView | None) -> dict[str, Any]:
    if job is None:
        return {
            "job_state": None,
            "job_result": None,
            "terminal": False,
        }
    terminal = is_remote_terminal(job)
    return {
        "job_state": job.remote_state,
        "job_result": job.remote_result,
        "terminal": terminal,
    }


def build_import_job_result(
    *,
    result: ImportJobExecutionResult,
    plan_content_identity: ContentIdentity,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "applied_count": result.applied_count,
        "dry_run": result.dry_run,
        "execution_mode": "import_v2",
        "job_id": result.job_id,
        "plan_content_identity": plan_content_identity.to_dict(),
        "result_schema": IMPORT_JOB_RESULT_SCHEMA,
        "result_version": IMPORT_JOB_RESULT_VERSION,
        "stale": False,
        "submission_state": result.submission_state,
        "submitted": submission_state_as_bool(result.submission_state),
        "success": result.success,
        **_job_fields(result.job),
    }
    if result.import_error_summary is not None:
        payload["import_error_summary"] = dict(result.import_error_summary)
    if result.error:
        payload["error"] = result.error
    return payload


def build_import_job_sync_payload(
    *,
    mode: str,
    result: ImportJobExecutionResult,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "applied_count": result.applied_count,
        "dry_run": result.dry_run,
        "execution_mode": "import_v2",
        "job_id": result.job_id,
        "mode": mode,
        "result_schema": IMPORT_JOB_RESULT_SCHEMA,
        "result_version": IMPORT_JOB_RESULT_VERSION,
        "submission_state": result.submission_state,
        "submitted": submission_state_as_bool(result.submission_state),
        "success": result.success,
        **_job_fields(result.job),
    }
    if result.import_error_summary is not None:
        payload["import_error_summary"] = dict(result.import_error_summary)
    if result.error:
        payload["error"] = result.error
    return payload


def format_import_job_result_human(payload: dict[str, Any]) -> str:
    job_id = payload.get("job_id")
    job_id_text = "null" if job_id is None else str(job_id)
    submission_state = payload.get("submission_state")
    if submission_state is None and "submitted" in payload:
        submission_state = "submitted" if payload["submitted"] else "not_attempted"
    lines = [
        f"stale={str(payload.get('stale', False)).lower()}",
        f"execution_mode={payload['execution_mode']}",
        f"dry_run={str(payload['dry_run']).lower()}",
        f"submission_state={submission_state}",
        f"job_id={job_id_text}",
        f"terminal={str(payload.get('terminal', False)).lower()}",
        f"success={str(payload['success']).lower()}",
        f"applied_count={payload['applied_count']}",
    ]
    if payload.get("job_state") is not None:
        lines.append(f"job_state={payload['job_state']}")
    if payload.get("job_result") is not None:
        lines.append(f"job_result={payload['job_result']}")
    summary = payload.get("import_error_summary")
    if isinstance(summary, dict) and "error_count" in summary:
        lines.append(f"error_count={summary['error_count']}")
    if payload.get("error"):
        lines.append(f"error={payload['error']}")
    return "\n".join(lines) + "\n"
