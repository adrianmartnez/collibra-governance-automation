"""Legacy Settings / mapping → resolved provider bindings (in-memory v1 compat)."""

from __future__ import annotations

from typing import Any

from governance.config import Settings
from governance.config_contract.provider_resolution import ResolvedProviderBinding
from governance.integrations.collibra.mapping import CollibraMappingConfig
from governance.providers.contracts import ProviderRuntimeContext
from governance.providers.registry import ProviderRegistry


def _mapping_config_object(mapping_config: CollibraMappingConfig) -> dict[str, Any]:
    identity = mapping_config.to_identity_dict()
    return {
        "domain_ref": identity["domain_ref"],
        "asset_type_refs": dict(identity["asset_type_refs"]),
        "relation_type_refs": dict(identity["relation_type_refs"]),
        "attribute_type_refs": dict(identity["attribute_type_refs"]),
    }


def postgresql_binding_from_settings(
    settings: Settings,
    registry: ProviderRegistry,
) -> ResolvedProviderBinding:
    """Resolved postgresql source binding for metadata_discovery (plaintext secrets)."""
    registration = registry.get("postgresql")
    config: dict[str, object] = {
        "host": settings.postgres_host,
        "port": settings.postgres_port,
        "database": settings.postgres_db,
        "user": settings.postgres_user,
        "password": settings.postgres_password,
        "source_name": settings.postgres_source_name,
    }
    runtime = ProviderRuntimeContext(config=config, config_root=None)
    return ResolvedProviderBinding(
        role="source",
        logical_id="postgresql",
        provider_id="postgresql",
        registration=registration,
        runtime_context=runtime,
        config_path="/legacy/postgresql",
    )


def collibra_binding_from_settings(
    settings: Settings,
    mapping_config: CollibraMappingConfig,
    registry: ProviderRegistry,
) -> ResolvedProviderBinding:
    """Resolved collibra target binding matching built-in factory config shape."""
    registration = registry.get("collibra")
    config: dict[str, object] = {
        "mode": settings.collibra_mode,
        "mapping": _mapping_config_object(mapping_config),
        "base_url": settings.collibra_base_url,
        "username": settings.collibra_username,
        "password": settings.collibra_password,
        "bearer_token": settings.collibra_bearer_token,
        "client_id": settings.collibra_client_id,
        "client_secret": settings.collibra_client_secret,
        "token_url": settings.collibra_token_url,
        "oauth_scope": settings.collibra_oauth_scope,
        "oauth_client_auth": settings.collibra_oauth_client_auth,
        "timeout_seconds": settings.collibra_timeout_seconds,
        "job_poll_interval_seconds": settings.collibra_job_poll_interval_seconds,
        "job_poll_timeout_seconds": settings.collibra_job_poll_timeout_seconds,
        "execution_mode": settings.collibra_execution_mode,
        "synchronization_id": settings.collibra_synchronization_id,
        "batch_max_resources": settings.collibra_batch_max_resources,
        "batch_max_additional_characteristics": (
            settings.collibra_batch_max_additional_characteristics
        ),
        "source_name": settings.postgres_source_name,
    }
    runtime = ProviderRuntimeContext(config=config, config_root=None)
    return ResolvedProviderBinding(
        role="target",
        logical_id="collibra",
        provider_id="collibra",
        registration=registration,
        runtime_context=runtime,
        config_path="/legacy/collibra",
    )


__all__ = [
    "collibra_binding_from_settings",
    "postgresql_binding_from_settings",
]
