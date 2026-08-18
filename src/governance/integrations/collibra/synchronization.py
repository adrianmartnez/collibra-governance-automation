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
    SYNC_BATCH_SUBMISSION_UNCERTAIN,
    BatchLifecycleRecord,
    JobView,
    SubmissionState,
    batch_lifecycle_projections,
    is_terminal_success,
    make_batch_lifecycle_record,
    observe_job,
    submission_state_as_bool,
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
    batch_lifecycle: tuple[BatchLifecycleRecord, ...]
    finalization_submission_state: SubmissionState
    finalization_job_id: str | None
    finalization_job: JobView | None
    success: bool
    applied_count: int
    unchanged_count: int
    import_error_summary: dict[str, int] | None = None
    error: str | None = None

    @property
    def batch_job_ids(self) -> tuple[str, ...]:
        job_ids, _, _ = batch_lifecycle_projections(self.batch_lifecycle)
        return job_ids

    @property
    def batch_jobs(self) -> tuple[JobView, ...]:
        _, jobs, _ = batch_lifecycle_projections(self.batch_lifecycle)
        return jobs

    @property
    def batch_submission_state(self) -> SubmissionState:
        _, _, submission_state = batch_lifecycle_projections(self.batch_lifecycle)
        return submission_state

    @property
    def finalization_submitted(self) -> bool | None:
        return submission_state_as_bool(self.finalization_submission_state)

    @property
    def report(self) -> SyncExecutionReport:
        return SyncExecutionReport(
            synchronization_id=self.synchronization_id,
            batch_job_ids=self.batch_job_ids,
            batch_outcomes=tuple(
                record.normalized_outcome or "unknown" for record in self.batch_lifecycle
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


def _sync_lifecycle_result(
    *,
    plan: SyncPlan,
    document: ImportDocument,
    sync_id: str,
    unchanged_count: int,
    batch_lifecycle: tuple[BatchLifecycleRecord, ...],
    finalization_submission_state: SubmissionState,
    finalization_job_id: str | None,
    finalization_job: JobView | None,
    success: bool,
    applied_count: int,
    error: str | None = None,
    adapter: Any | None = None,
    import_error_summary_job_id: str | None = None,
) -> SyncLifecycleResult:
    summary = None
    if adapter is not None and import_error_summary_job_id:
        summary = _best_effort_import_error_summary(adapter, import_error_summary_job_id)
    return SyncLifecycleResult(
        plan=plan,
        document=document,
        dry_run=False,
        synchronization_id=sync_id,
        batch_lifecycle=batch_lifecycle,
        finalization_submission_state=finalization_submission_state,
        finalization_job_id=finalization_job_id,
        finalization_job=finalization_job,
        success=success,
        applied_count=applied_count,
        unchanged_count=unchanged_count,
        import_error_summary=summary,
        error=error,
    )


def execute_sync_v2(
    adapter: Any,
    plan: SyncPlan,
    mapping_config: CollibraMappingConfig,
    *,
    apply: bool,
    synchronization_id: str,
    max_resources: int | None = None,
    max_additional_characteristics: int | None = None,
) -> SyncLifecycleResult:
    """Submit partitioned sync batches, poll each, then IGNORE-finalize and poll."""
    from governance.integrations.collibra.batching import (
        batch_document_counts,
        partition_document,
    )
    from governance.integrations.collibra.telemetry import emit

    sync_id = parse_synchronization_id(synchronization_id)
    document = compile_import_document(plan, mapping_config)
    unchanged_count = len(plan.unchanged)
    batches = partition_document(
        document,
        max_resources=max_resources,
        max_additional_characteristics=max_additional_characteristics,
    )
    if not apply:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=True,
            synchronization_id=sync_id,
            batch_lifecycle=(),
            finalization_submission_state="not_attempted",
            finalization_job_id=None,
            finalization_job=None,
            success=True,
            applied_count=0,
            unchanged_count=unchanged_count,
        )
    if not batches:
        return SyncLifecycleResult(
            plan=plan,
            document=document,
            dry_run=False,
            synchronization_id=sync_id,
            batch_lifecycle=(),
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
            batch_lifecycle=(),
            finalization_submission_state="not_attempted",
            finalization_job_id=None,
            finalization_job=None,
            success=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error="sync_v2 requires a live Collibra adapter",
        )

    prove_import_create_identifiers_absent(adapter, plan)

    applied = 0
    records: list[BatchLifecycleRecord] = []
    batch_count = len(batches)

    for batch_index, batch in enumerate(batches):
        try:
            batch_submission = submit_batch(sync_id, batch)
        except CollibraAdapterError:
            records.append(
                make_batch_lifecycle_record(
                    batch_index,
                    batch,
                    submission_state="unknown",
                )
            )
            return _sync_lifecycle_result(
                plan=plan,
                document=document,
                sync_id=sync_id,
                unchanged_count=unchanged_count,
                batch_lifecycle=tuple(records),
                finalization_submission_state="not_attempted",
                finalization_job_id=None,
                finalization_job=None,
                success=False,
                applied_count=applied,
                error=SYNC_BATCH_SUBMISSION_UNCERTAIN,
            )
        batch_job_id = getattr(batch_submission, "job_id", "") or ""
        if not batch_job_id:
            records.append(
                make_batch_lifecycle_record(
                    batch_index,
                    batch,
                    submission_state="unknown",
                )
            )
            return _sync_lifecycle_result(
                plan=plan,
                document=document,
                sync_id=sync_id,
                unchanged_count=unchanged_count,
                batch_lifecycle=tuple(records),
                finalization_submission_state="not_attempted",
                finalization_job_id=None,
                finalization_job=None,
                success=False,
                applied_count=applied,
                error=SYNC_BATCH_SUBMISSION_UNCERTAIN,
            )
        counts = batch_document_counts(batch)
        emit(
            operation="sync_batch",
            endpoint_family="sync_v2",
            batch_index=batch_index + 1,
            batch_count=batch_count,
            resource_count=counts.resource_count,
            additional_characteristic_count=counts.additional_characteristic_count,
            job_id=batch_job_id,
        )
        batch_view, observation_error = observe_job(poll_job, batch_job_id)
        if observation_error is not None:
            records.append(
                make_batch_lifecycle_record(
                    batch_index,
                    batch,
                    submission_state="submitted",
                    job_id=batch_job_id,
                    observation_error=observation_error,
                )
            )
            return _sync_lifecycle_result(
                plan=plan,
                document=document,
                sync_id=sync_id,
                unchanged_count=unchanged_count,
                batch_lifecycle=tuple(records),
                finalization_submission_state="not_attempted",
                finalization_job_id=None,
                finalization_job=None,
                success=False,
                applied_count=applied,
                error=observation_error,
            )
        assert batch_view is not None
        records.append(
            make_batch_lifecycle_record(
                batch_index,
                batch,
                submission_state="submitted",
                job_id=batch_job_id,
                job=batch_view,
            )
        )
        if not is_terminal_success(batch_view):
            outcome = batch_view.normalized_outcome or batch_view.normalized_state
            failing_id = batch_view.job_id or batch_job_id
            return _sync_lifecycle_result(
                plan=plan,
                document=document,
                sync_id=sync_id,
                unchanged_count=unchanged_count,
                batch_lifecycle=tuple(records),
                finalization_submission_state="not_attempted",
                finalization_job_id=None,
                finalization_job=None,
                success=False,
                applied_count=applied,
                error=f"sync batch job outcome={outcome}",
                adapter=adapter,
                import_error_summary_job_id=failing_id,
            )
        applied += len(batch.commands)

    try:
        finalize_submission = submit_finalize(sync_id, strategy=FINALIZATION_STRATEGY_IGNORE)
    except CollibraAdapterError:
        return _sync_lifecycle_result(
            plan=plan,
            document=document,
            sync_id=sync_id,
            unchanged_count=unchanged_count,
            batch_lifecycle=tuple(records),
            finalization_submission_state="unknown",
            finalization_job_id=None,
            finalization_job=None,
            success=False,
            applied_count=applied,
            error="sync finalization job outcome=uncertain",
        )
    finalize_job_id = getattr(finalize_submission, "job_id", "") or ""
    if not finalize_job_id:
        return _sync_lifecycle_result(
            plan=plan,
            document=document,
            sync_id=sync_id,
            unchanged_count=unchanged_count,
            batch_lifecycle=tuple(records),
            finalization_submission_state="unknown",
            finalization_job_id=None,
            finalization_job=None,
            success=False,
            applied_count=applied,
            error="sync finalization job outcome=uncertain",
        )
    finalize_view, finalize_observation_error = observe_job(poll_job, finalize_job_id)
    if finalize_observation_error is not None:
        return _sync_lifecycle_result(
            plan=plan,
            document=document,
            sync_id=sync_id,
            unchanged_count=unchanged_count,
            batch_lifecycle=tuple(records),
            finalization_submission_state="submitted",
            finalization_job_id=finalize_job_id,
            finalization_job=None,
            success=False,
            applied_count=applied,
            error=finalize_observation_error,
        )
    assert finalize_view is not None
    emit(
        operation="sync_finalize",
        endpoint_family="sync_v2",
        job_id=finalize_job_id,
        remote_state=finalize_view.remote_state,
        remote_result=finalize_view.remote_result,
        normalized_job_state=finalize_view.normalized_state,
        normalized_result=finalize_view.normalized_outcome,
    )
    if not is_terminal_success(finalize_view):
        outcome = finalize_view.normalized_outcome or finalize_view.normalized_state
        return _sync_lifecycle_result(
            plan=plan,
            document=document,
            sync_id=sync_id,
            unchanged_count=unchanged_count,
            batch_lifecycle=tuple(records),
            finalization_submission_state="submitted",
            finalization_job_id=finalize_job_id,
            finalization_job=finalize_view,
            success=False,
            applied_count=applied,
            error=f"sync finalization job outcome={outcome}",
            adapter=adapter,
            import_error_summary_job_id=records[-1].job_id if records else None,
        )
    return SyncLifecycleResult(
        plan=plan,
        document=document,
        dry_run=False,
        synchronization_id=sync_id,
        batch_lifecycle=tuple(records),
        finalization_submission_state="submitted",
        finalization_job_id=finalize_job_id,
        finalization_job=finalize_view,
        success=True,
        applied_count=applied,
        unchanged_count=unchanged_count,
    )
