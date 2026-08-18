"""Normalize documented Collibra Import/Core job state and result.

Remote enums follow the Import/Core OpenAPI contract. COMPLETED alone is never
success; terminal success requires state=COMPLETED and result=SUCCESS.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from governance.integrations.collibra.adapters import CollibraAdapterError

JOB_PATH_PREFIX = "/rest/2.0/jobs"
IMPORT_ERRORS_PATH_PREFIX = "/rest/2.0/import/results"

REMOTE_STATES = frozenset({"WAITING", "RUNNING", "CANCELING", "COMPLETED", "CANCELED", "ERROR"})
REMOTE_RESULTS = frozenset({"NOT_SET", "SUCCESS", "COMPLETED_WITH_ERROR", "FAILURE", "ABORTED"})
NON_TERMINAL_STATES = frozenset({"WAITING", "RUNNING", "CANCELING"})

NORMALIZED_NON_TERMINAL = "non_terminal"
NORMALIZED_TERMINAL_SUCCESS = "terminal_success"
NORMALIZED_TERMINAL_FAILURE = "terminal_failure"
NORMALIZED_CANCELLED = "cancelled"
NORMALIZED_UNCERTAIN = "uncertain"
NORMALIZED_UNKNOWN = "unknown"
NORMALIZED_TIMEOUT = "timeout"

OUTCOME_SUCCESS = "success"
OUTCOME_COMPLETED_WITH_ERROR = "completed_with_error"
OUTCOME_FAILURE = "failure"
OUTCOME_ABORTED = "aborted"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_UNKNOWN = "unknown"
OUTCOME_UNCERTAIN = "uncertain"
OUTCOME_TIMEOUT = "timeout"

DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_POLL_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class JobPollingPolicy:
    interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class JobView:
    """Inspectable job snapshot. Never includes response bodies or secrets."""

    job_id: str | None
    remote_state: str | None
    remote_result: str | None
    normalized_state: str
    normalized_outcome: str | None


def job_path(job_id: str) -> str:
    return f"{JOB_PATH_PREFIX}/{job_id}"


def import_errors_path(job_id: str) -> str:
    return f"{IMPORT_ERRORS_PATH_PREFIX}/{job_id}/errors"


def is_terminal_success(view: JobView) -> bool:
    return (
        view.normalized_state == NORMALIZED_TERMINAL_SUCCESS
        and view.normalized_outcome == OUTCOME_SUCCESS
    )


def classify_job(payload: Any, *, job_id: str | None = None) -> JobView:
    """Map a Collibra job JSON object to normalized state and outcome."""
    if not isinstance(payload, dict):
        return JobView(
            job_id=job_id,
            remote_state=None,
            remote_result=None,
            normalized_state=NORMALIZED_UNCERTAIN,
            normalized_outcome=OUTCOME_UNCERTAIN,
        )
    remote_id = str(payload.get("id") or job_id or "") or None
    raw_state = payload.get("state")
    raw_result = payload.get("result")
    state = raw_state.strip() if isinstance(raw_state, str) else None
    result = raw_result.strip() if isinstance(raw_result, str) else None
    if not state:
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_UNCERTAIN,
            normalized_outcome=OUTCOME_UNCERTAIN,
        )
    if state not in REMOTE_STATES:
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_UNKNOWN,
            normalized_outcome=OUTCOME_UNKNOWN,
        )
    if state in NON_TERMINAL_STATES:
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_NON_TERMINAL,
            normalized_outcome=None,
        )
    if state == "CANCELED":
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_CANCELLED,
            normalized_outcome=OUTCOME_CANCELLED,
        )
    if state == "ERROR":
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_TERMINAL_FAILURE,
            normalized_outcome=OUTCOME_FAILURE,
        )
    # COMPLETED
    if result is None or result == "" or result == "NOT_SET":
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_UNCERTAIN,
            normalized_outcome=OUTCOME_UNCERTAIN,
        )
    if result not in REMOTE_RESULTS:
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_UNKNOWN,
            normalized_outcome=OUTCOME_UNKNOWN,
        )
    if result == "SUCCESS":
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_TERMINAL_SUCCESS,
            normalized_outcome=OUTCOME_SUCCESS,
        )
    if result == "COMPLETED_WITH_ERROR":
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_TERMINAL_FAILURE,
            normalized_outcome=OUTCOME_COMPLETED_WITH_ERROR,
        )
    if result == "ABORTED":
        return JobView(
            job_id=remote_id,
            remote_state=state,
            remote_result=result,
            normalized_state=NORMALIZED_TERMINAL_FAILURE,
            normalized_outcome=OUTCOME_ABORTED,
        )
    return JobView(
        job_id=remote_id,
        remote_state=state,
        remote_result=result,
        normalized_state=NORMALIZED_TERMINAL_FAILURE,
        normalized_outcome=OUTCOME_FAILURE,
    )


def timeout_view(previous: JobView | None = None) -> JobView:
    return JobView(
        job_id=None if previous is None else previous.job_id,
        remote_state=None if previous is None else previous.remote_state,
        remote_result=None if previous is None else previous.remote_result,
        normalized_state=NORMALIZED_TIMEOUT,
        normalized_outcome=OUTCOME_TIMEOUT,
    )


def poll_until_terminal(
    fetch_job: Callable[[str], Any],
    job_id: str,
    *,
    monotonic_clock: Callable[[], float],
    sleeper: Callable[[float], None],
    interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
) -> JobView:
    """Poll GET /jobs/{id} until terminal, unknown, uncertain, or timeout.

    CANCELING remains non-terminal until the bound expires.
    """
    if not job_id.strip():
        return JobView(
            job_id=None,
            remote_state=None,
            remote_result=None,
            normalized_state=NORMALIZED_UNCERTAIN,
            normalized_outcome=OUTCOME_UNCERTAIN,
        )
    started = monotonic_clock()
    last: JobView | None = None
    while True:
        elapsed = monotonic_clock() - started
        if elapsed >= timeout_seconds:
            return timeout_view(last)
        payload = fetch_job(job_id)
        last = classify_job(payload, job_id=job_id)
        if last.normalized_state != NORMALIZED_NON_TERMINAL:
            return last
        remaining = timeout_seconds - (monotonic_clock() - started)
        if remaining <= 0:
            return timeout_view(last)
        sleeper(min(interval_seconds, remaining))


def sanitize_import_error_summary(payload: Any) -> dict[str, int]:
    """Return a secret-safe import-error summary. Never includes row payloads."""
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return {"error_count": len(results)}
        total = payload.get("total")
        if isinstance(total, int) and total >= 0:
            return {"error_count": total}
    if isinstance(payload, list):
        return {"error_count": len(payload)}
    return {"error_count": 0}


class CollibraJobError(CollibraAdapterError):
    """Job lifecycle failure that omits remote bodies and secrets."""
