"""Explicit Collibra synchronization v2 lifecycle (IGNORE-only finalization).

This module never uses the combined /import/synchronize/{id}/json-job endpoint.
Overall success requires the finalization job to be COMPLETED+SUCCESS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from governance.config import Settings
from governance.integrations.collibra.adapters import CollibraAdapterError
from governance.integrations.collibra.endpoint import normalize_base_url
from governance.integrations.collibra.import_api import (
    ImportDocument,
    _best_effort_import_error_summary,
    compile_import_document,
    prove_import_create_identifiers_absent,
)
from governance.integrations.collibra.jobs import (
    JobView,
    SubmissionState,
    is_terminal_success,
    observe_job,
)
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import SyncPlan

GOV_SYNC_NAMESPACE = UUID("a4f8c2d1-9e3b-4c7a-8f16-2d5e9b1a7c04")
FINALIZATION_STRATEGY_IGNORE = "IGNORE"
FORBIDDEN_FINALIZATION_STRATEGIES = frozenset({"REMOVE_RESOURCES", "CHANGE_STATUS"})


@dataclass(frozen=True, slots=True)
class SyncExecutionReport:
    """Secret-safe resume/inspection state for a sync_v2 run."""

    synchronization_id: str
    batch_job_ids: tuple[str, ...]
    batch_outcomes: tuple[str, ...]
    finalization_job_id: str | None
    finalization_outcome: str | None


@dataclass(frozen=True, slots=True)
class SyncLifecycleResult:
    """sync_v2 batch submit, poll, IGNORE finalize, and finalization poll."""

    plan: SyncPlan
    document: ImportDocument
    dry_run: bool
    synchronization_id: str
    batch_job_ids: tuple[str, ...]
    batch_jobs: tuple[JobView, ...]
    finalization_submission_state: SubmissionState
    finalization_job_id: str | None
    finalization_job: JobView | None
    success: bool
    applied_count: int
    unchanged_count: int
    import_error_summary: dict[str, int] | None = None
    error: str | None = None

    @property
    def finalization_submitted(self) -> bool:
        return self.finalization_submission_state == "submitted"

    @property
    def report(self) -> SyncExecutionReport:
        return SyncExecutionReport(
            synchronization_id=self.synchronization_id,
            batch_job_ids=self.batch_job_ids,
            batch_outcomes=tuple(
                job.normalized_outcome or job.normalized_state for job in self.batch_jobs
            ),
            finalization_job_id=self.finalization_job_id,
            finalization_outcome=(
                None
                if self.finalization_job is None
                else (
                    self.finalization_job.normalized_outcome
                    or self.finalization_job.normalized_state
                )
            ),
        )


def derive_synchronization_id(*, provider: str, source_name: str, endpoint: str) -> str:
    """Stable UUID5 from provider, source name, and normalized endpoint."""
    key = f"{provider}|{source_name}|{endpoint}"
    return str(uuid5(GOV_SYNC_NAMESPACE, key))


def parse_synchronization_id(value: str) -> str:
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError) as exc:
        raise ValueError("collibra_synchronization_id must be a UUID") from exc


def effective_synchronization_id(settings: Settings) -> str:
    """Resolved UUID used for remote sync_v2 identity (override or derived)."""
    raw = (settings.collibra_synchronization_id or "").strip()
    if raw:
        return parse_synchronization_id(raw)
    endpoint = ""
    if settings.collibra_mode.strip().lower() == "live":
        endpoint = normalize_base_url(settings.collibra_base_url)
    return derive_synchronization_id(
        provider="collibra",
        source_name=settings.postgres_source_name,
        endpoint=endpoint,
    )


def is_combined_synchronize_json_job(path: str) -> bool:
    """True for the forbidden import-and-finalize combined endpoint."""
    marker = "/import/synchronize/"
    if marker not in path:
        return False
    rest = path.split(marker, 1)[1].split("?", 1)[0].strip("/")
    parts = rest.split("/")
    return len(parts) == 2 and parts[1] == "json-job"


def require_ignore_strategy(strategy: str) -> str:
    value = (strategy or "").strip()
    if value in FORBIDDEN_FINALIZATION_STRATEGIES:
        raise CollibraAdapterError(
            "finalizationStrategy is forbidden",
            operation="submit_sync_finalize",
            endpoint_path="/rest/2.0/import/synchronize/{id}/finalize/job",
        )
    if value != FINALIZATION_STRATEGY_IGNORE:
        raise CollibraAdapterError(
            "finalizationStrategy must be IGNORE",
            operation="submit_sync_finalize",
            endpoint_path="/rest/2.0/import/synchronize/{id}/finalize/job",
        )
    return FINALIZATION_STRATEGY_IGNORE


def _batch_failure(
    *,
    plan: SyncPlan,
    document: ImportDocument,
    sync_id: str,
    unchanged_count: int,
    batch_job_ids: tuple[str, ...],
    batch_jobs: tuple[JobView, ...],
    adapter: Any,
    batch_view: JobView,
    error: str,
) -> SyncLifecycleResult:
    failing_id = batch_view.job_id or (batch_job_ids[-1] if batch_job_ids else "")
    return SyncLifecycleResult(
        plan=plan,
        document=document,
        dry_run=False,
        synchronization_id=sync_id,
        batch_job_ids=batch_job_ids,
        batch_jobs=batch_jobs,
        finalization_submission_state="not_attempted",
        finalization_job_id=None,
        finalization_job=None,
        success=False,
        applied_count=0,
        unchanged_count=unchanged_count,
        import_error_summary=_best_effort_import_error_summary(adapter, failing_id),
        error=error,
    )


def execute_sync_v2(
    adapter: Any,
    plan: SyncPlan,
    mapping_config: CollibraMappingConfig,
    *,
    apply: bool,
    synchronization_id: str,
) -> SyncLifecycleResult:
    """Submit one sync batch, poll it, then IGNORE-finalize and poll that job."""
    sync_id = parse_synchronization_id(synchronization_id)
    document = compile_import_document(plan, mapping_config)
    unchanged_count = len(plan.unchanged)
    if not apply:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=True,
            synchronization_id=sync_id,
            batch_job_ids=(),
            batch_jobs=(),
            finalization_submission_state="not_attempted",
            finalization_job_id=None,
            finalization_job=None,
            success=True,
            applied_count=0,
            unchanged_count=unchanged_count,
        )
    if not document.commands:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=False,
            synchronization_id=sync_id,
            batch_job_ids=(),
            batch_jobs=(),
            finalization_submission_state="not_attempted",
            finalization_job_id=None,
            finalization_job=None,
            success=True,
            applied_count=0,
            unchanged_count=unchanged_count,
        )

    submit_batch = getattr(adapter, "submit_sync_batch", None)
    poll_job = getattr(adapter, "poll_job", None)
    submit_finalize = getattr(adapter, "submit_sync_finalize", None)
    if submit_batch is None or poll_job is None or submit_finalize is None:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=False,
            synchronization_id=sync_id,
            batch_job_ids=(),
            batch_jobs=(),
            finalization_submission_state="not_attempted",
            finalization_job_id=None,
            finalization_job=None,
            success=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error="sync_v2 requires a live Collibra adapter",
        )

    prove_import_create_identifiers_absent(adapter, plan)
    batch_submission = submit_batch(sync_id, document)
    batch_job_id = getattr(batch_submission, "job_id", "") or ""
    if not batch_job_id:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=False,
            synchronization_id=sync_id,
            batch_job_ids=(),
            batch_jobs=(),
            finalization_submission_state="not_attempted",
            finalization_job_id=None,
            finalization_job=None,
            success=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error="sync batch job outcome=uncertain",
        )
    batch_view, observation_error = observe_job(poll_job, batch_job_id)
    if observation_error is not None:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=False,
            synchronization_id=sync_id,
            batch_job_ids=(batch_job_id,),
            batch_jobs=(),
            finalization_submission_state="not_attempted",
            finalization_job_id=None,
            finalization_job=None,
            success=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error=observation_error,
        )
    assert batch_view is not None
    if not is_terminal_success(batch_view):
        outcome = batch_view.normalized_outcome or batch_view.normalized_state
        return _batch_failure(
            plan=plan,
            document=document,
            sync_id=sync_id,
            unchanged_count=unchanged_count,
            batch_job_ids=(batch_job_id,),
            batch_jobs=(batch_view,),
            adapter=adapter,
            batch_view=batch_view,
            error=f"sync batch job outcome={outcome}",
        )

    try:
        finalize_submission = submit_finalize(sync_id, strategy=FINALIZATION_STRATEGY_IGNORE)
    except CollibraAdapterError:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=False,
            synchronization_id=sync_id,
            batch_job_ids=(batch_job_id,),
            batch_jobs=(batch_view,),
            finalization_submission_state="unknown",
            finalization_job_id=None,
            finalization_job=None,
            success=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error="sync finalization job outcome=uncertain",
        )
    finalize_job_id = getattr(finalize_submission, "job_id", "") or ""
    if not finalize_job_id:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=False,
            synchronization_id=sync_id,
            batch_job_ids=(batch_job_id,),
            batch_jobs=(batch_view,),
            finalization_submission_state="unknown",
            finalization_job_id=None,
            finalization_job=None,
            success=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error="sync finalization job outcome=uncertain",
        )
    finalize_view, finalize_observation_error = observe_job(poll_job, finalize_job_id)
    if finalize_observation_error is not None:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=False,
            synchronization_id=sync_id,
            batch_job_ids=(batch_job_id,),
            batch_jobs=(batch_view,),
            finalization_submission_state="submitted",
            finalization_job_id=finalize_job_id,
            finalization_job=None,
            success=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error=finalize_observation_error,
        )
    assert finalize_view is not None
    if not is_terminal_success(finalize_view):
        outcome = finalize_view.normalized_outcome or finalize_view.normalized_state
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=False,
            synchronization_id=sync_id,
            batch_job_ids=(batch_job_id,),
            batch_jobs=(batch_view,),
            finalization_submission_state="submitted",
            finalization_job_id=finalize_job_id,
            finalization_job=finalize_view,
            success=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            import_error_summary=_best_effort_import_error_summary(adapter, batch_job_id),
            error=f"sync finalization job outcome={outcome}",
        )
    return SyncLifecycleResult(
        plan=plan,
        document=document,
        dry_run=False,
        synchronization_id=sync_id,
        batch_job_ids=(batch_job_id,),
        batch_jobs=(batch_view,),
        finalization_submission_state="submitted",
        finalization_job_id=finalize_job_id,
        finalization_job=finalize_view,
        success=True,
        applied_count=len(document.commands),
        unchanged_count=unchanged_count,
    )
