"""Normalize validated governance.yaml into CanonicalConfig."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from governance.config_contract.models import (
    ArtifactsConfig,
    CanonicalConfig,
    CollibraAuthRefs,
    CollibraTargetConfig,
    PoliciesConfig,
    PostgresConnectionRefs,
    PostgresSourceConfig,
    SourceConfig,
    TargetConfig,
)
from governance.config_contract.paths import normalize_relative_path

DEFAULT_INVENTORY_PATH = "artifacts/metadata-inventory.json"
DEFAULT_SNAPSHOT_PATH = "artifacts/governance-snapshot.json"


def normalize_document(document: dict[str, Any], *, config_path: Path) -> CanonicalConfig:
    config_root = str(config_path.resolve().parent)

    source_raw = document["sources"][0]
    source = SourceConfig(
        id=str(source_raw["id"]),
        provider="postgresql",
        config=_normalize_postgres_config(source_raw["config"]),
    )

    targets: list[TargetConfig] = []
    if "targets" in document:
        target_raw = document["targets"][0]
        targets.append(
            TargetConfig(
                id=str(target_raw["id"]),
                provider="collibra",
                config=_normalize_collibra_config(target_raw["config"]),
            )
        )

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
    files_raw = policies_raw.get("files", [])
    policy_files = tuple(
        normalize_relative_path(item, pointer=f"/policies/files/{index}")
        for index, item in enumerate(files_raw or [])
    )

    return CanonicalConfig(
        schema_version="1",
        sources=(source,),
        targets=tuple(targets),
        artifacts=ArtifactsConfig(
            inventory_path=inventory_path,
            snapshot_path=snapshot_path,
        ),
        policies=PoliciesConfig(files=policy_files),
        config_root=config_root,
    )


def _normalize_postgres_config(config: dict[str, Any]) -> PostgresSourceConfig:
    connection_raw = config["connection"]
    connection = PostgresConnectionRefs(
        database_url_env=connection_raw.get("database_url_env"),
        host_env=connection_raw.get("host_env"),
        port_env=connection_raw.get("port_env"),
        db_env=connection_raw.get("db_env"),
        user_env=connection_raw.get("user_env"),
        password_env=connection_raw.get("password_env"),
    )
    return PostgresSourceConfig(
        connection=connection,
        source_name=config.get("source_name"),
        source_name_env=config.get("source_name_env"),
    )


def _normalize_collibra_config(config: dict[str, Any]) -> CollibraTargetConfig:
    mapping_path = normalize_relative_path(
        config["mapping"]["path"],
        pointer="/targets/0/config/mapping/path",
    )
    auth_raw = config.get("auth")
    auth = None
    if isinstance(auth_raw, dict):
        auth = CollibraAuthRefs(
            base_url_env=auth_raw.get("base_url_env"),
            username_env=auth_raw.get("username_env"),
            password_env=auth_raw.get("password_env"),
            bearer_token_env=auth_raw.get("bearer_token_env"),
            client_id_env=auth_raw.get("client_id_env"),
            client_secret_env=auth_raw.get("client_secret_env"),
            token_url_env=auth_raw.get("token_url_env"),
            scope_env=auth_raw.get("scope_env"),
            oauth_client_auth_env=auth_raw.get("oauth_client_auth_env"),
            timeout_seconds_env=auth_raw.get("timeout_seconds_env"),
        )
    return CollibraTargetConfig(
        mapping_path=mapping_path,
        mode_env=config.get("mode_env"),
        auth=auth,
    )
