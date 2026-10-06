"""Normalize validated governance.yaml v2 into CanonicalConfigV2 + locations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governance.config_contract.env_refs import normalize_provider_config
from governance.config_contract.models import ArtifactsConfig, AuthorityConfig, PoliciesConfig
from governance.config_contract.models_v2 import (
    CanonicalConfigV2,
    EffectiveProviderLocations,
    LoadedConfigV2,
    ProviderBoundConfig,
)
from governance.config_contract.paths import normalize_relative_path

DEFAULT_INVENTORY_PATH = "artifacts/metadata-inventory.json"
DEFAULT_SNAPSHOT_PATH = "artifacts/governance-snapshot.json"


def capture_effective_locations(document: dict[str, Any]) -> EffectiveProviderLocations:
    """Capture JSON Pointer bases from post-profile document before canonical sort."""
    source_locations: dict[str, str] = {}
    for index, entry in enumerate(document.get("sources") or []):
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            source_locations[entry["id"]] = f"/sources/{index}"
    target_locations: dict[str, str] = {}
    for index, entry in enumerate(document.get("targets") or []):
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            target_locations[entry["id"]] = f"/targets/{index}"
    return EffectiveProviderLocations(
        source_locations=source_locations,
        target_locations=target_locations,
    )


def normalize_document_v2(document: dict[str, Any], *, config_path: Path) -> LoadedConfigV2:
    locations = capture_effective_locations(document)
    config_root = str(config_path.resolve().parent)

    sources = [
        ProviderBoundConfig(
            id=str(entry["id"]),
            provider_id=str(entry["provider"]),
            config=normalize_provider_config(entry["config"]),
        )
        for entry in document["sources"]
    ]
    sources.sort(key=lambda item: item.id)

    targets: list[ProviderBoundConfig] = []
    if "targets" in document:
        targets = [
            ProviderBoundConfig(
                id=str(entry["id"]),
                provider_id=str(entry["provider"]),
                config=normalize_provider_config(entry["config"]),
            )
            for entry in document["targets"]
        ]
        targets.sort(key=lambda item: item.id)

    artifacts_raw = document.get("artifacts") or {}
    inventory_path = normalize_relative_path(
        artifacts_raw.get("inventory_path", DEFAULT_INVENTORY_PATH),
        pointer="/artifacts/inventory_path",
    )
    snapshot_path = normalize_relative_path(
        artifacts_raw.get("snapshot_path", DEFAULT_SNAPSHOT_PATH),
        pointer="/artifacts/snapshot_path",
    )

    policies_raw = document.get("policies") or {}
    policy_files = tuple(
        normalize_relative_path(item, pointer=f"/policies/files/{index}")
        for index, item in enumerate(policies_raw.get("files", []) or [])
    )
    authority_raw = document.get("authority") or {}
    authority_files = tuple(
        normalize_relative_path(item, pointer=f"/authority/files/{index}")
        for index, item in enumerate(authority_raw.get("files", []) or [])
    )

    canonical = CanonicalConfigV2(
        schema_version="2",
        sources=tuple(sources),
        targets=tuple(targets),
        artifacts=ArtifactsConfig(inventory_path=inventory_path, snapshot_path=snapshot_path),
        policies=PoliciesConfig(files=policy_files),
        config_root=config_root,
        authority=AuthorityConfig(files=authority_files),
    )
    return LoadedConfigV2(canonical=canonical, locations=locations)
