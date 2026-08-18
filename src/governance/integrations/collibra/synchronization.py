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
from governance.integrations.collibra.import_api import compile_import_document
from governance.integrations.collibra.jobs import is_terminal_success
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.integrations.collibra.models import SyncPlan, SyncResult

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


def execute_sync_v2(
    adapter: Any,
    plan: SyncPlan,
    mapping_config: CollibraMappingConfig,
    *,
    apply: bool,
    synchronization_id: str,
) -> SyncResult:
    """Submit one sync batch, poll it, then IGNORE-finalize and poll that job."""
    sync_id = parse_synchronization_id(synchronization_id)
    document = compile_import_document(plan, mapping_config)
    unchanged_count = len(plan.unchanged)
    if not apply:
        return SyncResult(
            success=True,
            dry_run=True,
            applied_count=0,
            unchanged_count=unchanged_count,
            plan=plan,
        )
    if not document.commands:
        return SyncResult(
            success=True,
            dry_run=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            plan=plan,
        )

    submit_batch = getattr(adapter, "submit_sync_batch", None)
    poll_job = getattr(adapter, "poll_job", None)
    submit_finalize = getattr(adapter, "submit_sync_finalize", None)
    if submit_batch is None or poll_job is None or submit_finalize is None:
        return SyncResult(
            success=False,
            dry_run=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error="sync_v2 requires a live Collibra adapter",
            plan=plan,
        )

    batch_submission = submit_batch(sync_id, document)
    batch_job_id = getattr(batch_submission, "job_id", "") or ""
    if not batch_job_id:
        return SyncResult(
            success=False,
            dry_run=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error="sync batch job outcome=uncertain",
            plan=plan,
        )
    batch_view = poll_job(batch_job_id)
    if not is_terminal_success(batch_view):
        outcome = batch_view.normalized_outcome or batch_view.normalized_state
        return SyncResult(
            success=False,
            dry_run=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error=f"sync batch job outcome={outcome}",
            plan=plan,
        )

    finalize_submission = submit_finalize(sync_id, strategy=FINALIZATION_STRATEGY_IGNORE)
    finalize_job_id = getattr(finalize_submission, "job_id", "") or ""
    if not finalize_job_id:
        return SyncResult(
            success=False,
            dry_run=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error="sync finalization job outcome=uncertain",
            plan=plan,
        )
    finalize_view = poll_job(finalize_job_id)
    if not is_terminal_success(finalize_view):
        outcome = finalize_view.normalized_outcome or finalize_view.normalized_state
        return SyncResult(
            success=False,
            dry_run=False,
            applied_count=0,
            unchanged_count=unchanged_count,
            error=f"sync finalization job outcome={outcome}",
            plan=plan,
        )
    return SyncResult(
        success=True,
        dry_run=False,
        applied_count=len(document.commands),
        unchanged_count=unchanged_count,
        plan=plan,
    )
