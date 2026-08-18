"""Additive import_v2 submission result. Distinct from apply-result v1.

This contract reports json-job SUBMITTED, not Collibra import job completion.
``job_terminal_status`` is always ``not_observed`` on this branch.
"""

from __future__ import annotations

from typing import Any

from governance.identity.hashing import ContentIdentity
from governance.integrations.collibra.import_api import ImportExecutionResult

IMPORT_SUBMISSION_RESULT_SCHEMA = "governance-import-submission-result"
IMPORT_SUBMISSION_RESULT_VERSION = "1"
JOB_TERMINAL_STATUS_NOT_OBSERVED = "not_observed"


def _submission_fields(result: ImportExecutionResult) -> dict[str, Any]:
    return {
        "applied_count": 0,
        "dry_run": result.dry_run,
        "execution_mode": "import_v2",
        "job_id": result.job_id,
        "job_terminal_status": JOB_TERMINAL_STATUS_NOT_OBSERVED,
        "submitted": result.submitted,
    }


def build_import_submission_result(
    *,
    result: ImportExecutionResult,
    plan_content_identity: ContentIdentity,
) -> dict[str, Any]:
    payload = {
        **_submission_fields(result),
        "plan_content_identity": plan_content_identity.to_dict(),
        "result_schema": IMPORT_SUBMISSION_RESULT_SCHEMA,
        "result_version": IMPORT_SUBMISSION_RESULT_VERSION,
        "stale": False,
    }
    if result.dry_run:
        payload["document"] = result.document.to_list()
    if result.error:
        payload["error"] = result.error
    return payload


def build_import_sync_payload(
    *,
    mode: str,
    result: ImportExecutionResult,
) -> dict[str, Any]:
    payload = {
        **_submission_fields(result),
        "mode": mode,
        "result_schema": IMPORT_SUBMISSION_RESULT_SCHEMA,
        "result_version": IMPORT_SUBMISSION_RESULT_VERSION,
    }
    if result.dry_run:
        payload["document"] = result.document.to_list()
    if result.error:
        payload["error"] = result.error
    return payload


def format_import_submission_human(payload: dict[str, Any]) -> str:
    job_id = payload.get("job_id")
    job_id_text = "null" if job_id is None else str(job_id)
    lines = [
        f"stale={str(payload.get('stale', False)).lower()}",
        f"execution_mode={payload['execution_mode']}",
        f"dry_run={str(payload['dry_run']).lower()}",
        f"submitted={str(payload['submitted']).lower()}",
        f"job_id={job_id_text}",
        f"job_terminal_status={payload['job_terminal_status']}",
        f"applied_count={payload['applied_count']}",
    ]
    if payload.get("error"):
        lines.append(f"error={payload['error']}")
    return "\n".join(lines) + "\n"


def format_import_sync_human(payload: dict[str, Any]) -> str:
    job_id = payload.get("job_id")
    job_id_text = "null" if job_id is None else str(job_id)
    lines = [
        f"mode={payload['mode']}",
        f"execution_mode={payload['execution_mode']}",
        f"dry_run={str(payload['dry_run']).lower()}",
        f"submitted={str(payload['submitted']).lower()}",
        f"job_id={job_id_text}",
        f"job_terminal_status={payload['job_terminal_status']}",
        f"applied_count={payload['applied_count']}",
    ]
    if payload.get("error"):
        lines.append(f"error={payload['error']}")
    return "\n".join(lines) + "\n"
