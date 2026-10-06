"""Effective Collibra target context for saved-plan binding."""

from __future__ import annotations

from typing import Any, Literal

from governance.config import Settings
from governance.integrations.collibra.endpoint import normalize_base_url

Mode = Literal["mock", "live"]


def build_collibra_target_context_projection(
    *,
    mode: str,
    base_url: str,
    execution_mode: str,
    synchronization_id: str,
    source_name: str = "",
) -> dict[str, Any]:
    """Pure target-context projection (no Settings, no credentials).

    ``synchronization_id`` is the *effective* UUID when ``execution_mode`` is
    ``sync_v2`` (override or already derived). Empty string means derive from
    ``source_name`` + normalized endpoint (v1 Settings path supplies derivation
    via ``effective_synchronization_id`` before calling this helper).
    """
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"mock", "live"}:
        raise ValueError("collibra_mode must be 'mock' or 'live'")
    if normalized_mode == "mock":
        projection: dict[str, Any] = {"endpoint": None, "mode": "mock", "provider": "collibra"}
    else:
        endpoint = normalize_base_url(base_url)
        projection = {"endpoint": endpoint, "mode": "live", "provider": "collibra"}

    execution = (execution_mode or "core_rest").strip()
    if execution == "import_v2":
        projection["execution"] = "import_v2"
    elif execution == "sync_v2":
        from governance.integrations.collibra.synchronization import (
            derive_synchronization_id,
            parse_synchronization_id,
        )

        projection["execution"] = "sync_v2"
        raw = (synchronization_id or "").strip()
        if raw:
            projection["effective_synchronization_id"] = parse_synchronization_id(raw)
        else:
            endpoint_value = "" if normalized_mode == "mock" else str(projection["endpoint"] or "")
            projection["effective_synchronization_id"] = derive_synchronization_id(
                provider="collibra",
                source_name=source_name,
                endpoint=endpoint_value,
            )
    return projection


def build_target_context_projection(settings: Settings) -> dict[str, Any]:
    """Build hash preimage for target_context_identity (may raise ValueError)."""
    from governance.integrations.collibra.synchronization import effective_synchronization_id

    mode = settings.collibra_mode.strip().lower()
    execution = getattr(settings, "collibra_execution_mode", "core_rest")
    sync_id = ""
    if execution == "sync_v2":
        # Preserve exact v1 derivation (override or derived UUID).
        sync_id = effective_synchronization_id(settings)
    return build_collibra_target_context_projection(
        mode=mode,
        base_url=settings.collibra_base_url,
        execution_mode=execution,
        synchronization_id=sync_id,
        source_name=settings.postgres_source_name,
    )


def target_context_public(projection: dict[str, Any]) -> dict[str, str]:
    """Non-secret inspectable fields persisted in .gplan."""
    return {
        "mode": str(projection["mode"]),
        "provider": str(projection["provider"]),
    }
