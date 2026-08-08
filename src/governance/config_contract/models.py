"""Canonical effective governance configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class PostgresConnectionRefs:
    database_url_env: str | None = None
    host_env: str | None = None
    port_env: str | None = None
    db_env: str | None = None
    user_env: str | None = None
    password_env: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        if self.database_url_env is not None:
            payload["database_url_env"] = self.database_url_env
        if self.host_env is not None:
            payload["host_env"] = self.host_env
        if self.port_env is not None:
            payload["port_env"] = self.port_env
        if self.db_env is not None:
            payload["db_env"] = self.db_env
        if self.user_env is not None:
            payload["user_env"] = self.user_env
        if self.password_env is not None:
            payload["password_env"] = self.password_env
        return payload


@dataclass(frozen=True, slots=True)
class PostgresSourceConfig:
    connection: PostgresConnectionRefs
    source_name: str | None = None
    source_name_env: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"connection": self.connection.to_dict()}
        if self.source_name is not None:
            payload["source_name"] = self.source_name
        if self.source_name_env is not None:
            payload["source_name_env"] = self.source_name_env
        return payload


@dataclass(frozen=True, slots=True)
class SourceConfig:
    id: str
    provider: Literal["postgresql"]
    config: PostgresSourceConfig

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "id": self.id,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class CollibraAuthRefs:
    base_url_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    bearer_token_env: str | None = None
    timeout_seconds_env: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        for key in (
            "base_url_env",
            "username_env",
            "password_env",
            "bearer_token_env",
            "timeout_seconds_env",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


@dataclass(frozen=True, slots=True)
class CollibraTargetConfig:
    mapping_path: str
    mode_env: str | None = None
    auth: CollibraAuthRefs | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"mapping": {"path": self.mapping_path}}
        if self.mode_env is not None:
            payload["mode_env"] = self.mode_env
        if self.auth is not None and self.auth.to_dict():
            payload["auth"] = self.auth.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class TargetConfig:
    id: str
    provider: Literal["collibra"]
    config: CollibraTargetConfig

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "id": self.id,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class ArtifactsConfig:
    inventory_path: str
    snapshot_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "inventory_path": self.inventory_path,
            "snapshot_path": self.snapshot_path,
        }


@dataclass(frozen=True, slots=True)
class PoliciesConfig:
    files: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        return {"files": list(self.files)}


@dataclass(frozen=True, slots=True)
class CanonicalConfig:
    """Effective normalized configuration (no secrets, no absolute paths)."""

    schema_version: str
    sources: tuple[SourceConfig, ...]
    targets: tuple[TargetConfig, ...]
    artifacts: ArtifactsConfig
    policies: PoliciesConfig
    config_root: str

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "artifacts": self.artifacts.to_dict(),
            "policies": self.policies.to_dict(),
            "schema_version": self.schema_version,
            "sources": [source.to_dict() for source in self.sources],
        }
        if self.targets:
            payload["targets"] = [target.to_dict() for target in self.targets]
        return payload

    def identity_projection(self) -> dict[str, Any]:
        """Governance-relevant projection used for config_identity."""
        payload: dict[str, Any] = {
            "policies": self.policies.to_dict(),
            "schema_version": self.schema_version,
            "sources": [source.to_dict() for source in self.sources],
        }
        if self.targets:
            payload["targets"] = [target.to_dict() for target in self.targets]
        return payload
