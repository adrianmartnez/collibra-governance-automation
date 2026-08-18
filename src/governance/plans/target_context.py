"""Effective Collibra target context for saved-plan binding."""

from __future__ import annotations

from typing import Any, Literal

from governance.config import Settings
from governance.integrations.collibra.endpoint import normalize_base_url

Mode = Literal["mock", "live"]


def build_target_context_projection(settings: Settings) -> dict[str, Any]:
    """Build hash preimage for target_context_identity (may raise ValueError)."""
    mode = settings.collibra_mode.strip().lower()
    if mode not in {"mock", "live"}:
        raise ValueError("collibra_mode must be 'mock' or 'live'")
    if mode == "mock":
        projection: dict[str, Any] = {"endpoint": None, "mode": "mock", "provider": "collibra"}
    else:
        endpoint = normalize_base_url(settings.collibra_base_url)
        projection = {"endpoint": endpoint, "mode": "live", "provider": "collibra"}
    execution = getattr(settings, "collibra_execution_mode", "core_rest")
    if execution == "import_v2":
        projection["execution"] = "import_v2"
    return projection


def target_context_public(projection: dict[str, Any]) -> dict[str, str]:
    """Non-secret inspectable fields persisted in .gplan."""
    return {
        "mode": str(projection["mode"]),
        "provider": str(projection["provider"]),
    }
