"""Version-aware governance.yaml runtime configuration loading."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from governance.config_contract.errors import (
    CODE_SCHEMA,
    CODE_UNSUPPORTED,
    ConfigSchemaError,
    DiagnosticError,
    UnsupportedConfigVersionError,
)
from governance.config_contract.models import CanonicalConfig
from governance.config_contract.models_v2 import LoadedConfigV2
from governance.config_contract.normalize import normalize_document
from governance.config_contract.normalize_v2 import normalize_document_v2
from governance.config_contract.parse import parse_governance_yaml
from governance.config_contract.profiles import apply_profile_overlay, select_profile_name
from governance.config_contract.provider_resolution import (
    ResolvedProviderConfiguration,
    resolve_provider_configuration,
)
from governance.config_contract.schema import validate_structure
from governance.config_contract.semantic import validate_semantics
from governance.config_contract.semantic_v2 import validate_semantics_v2
from governance.providers.registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class V1RuntimeConfiguration:
    kind: Literal["1"]
    canonical: CanonicalConfig


@dataclass(frozen=True, slots=True)
class V2RuntimeConfiguration:
    kind: Literal["2"]
    loaded: LoadedConfigV2


RuntimeConfiguration = V1RuntimeConfiguration | V2RuntimeConfiguration


def _declared_schema_version(document: Any) -> str:
    if not isinstance(document, dict):
        raise ConfigSchemaError(
            [
                DiagnosticError(
                    code=CODE_SCHEMA,
                    path="",
                    message="configuration root must be a mapping",
                )
            ]
        )
    declared = document.get("schema_version", "1")
    if declared == "1" or declared == "2":
        return declared
    raise UnsupportedConfigVersionError(
        [
            DiagnosticError(
                code=CODE_UNSUPPORTED,
                path="/schema_version",
                message="unsupported configuration schema_version",
            )
        ]
    )


def _load_v1_from_document(
    document: dict[str, Any],
    config_path: Path,
    *,
    profile: str | None,
    environ: Mapping[str, str],
) -> CanonicalConfig:
    validate_structure(document, version="1")
    selected = select_profile_name(
        cli_profile=profile,
        env_profile=environ.get("GOVERNANCE_PROFILE"),
    )
    effective = apply_profile_overlay(document, selected)
    validate_structure(effective, version="1")
    validate_semantics(effective)
    return normalize_document(effective, config_path=config_path)


def _load_v2_from_document(
    document: dict[str, Any],
    config_path: Path,
    *,
    profile: str | None,
    environ: Mapping[str, str],
) -> LoadedConfigV2:
    validate_structure(document, version="2")
    selected = select_profile_name(
        cli_profile=profile,
        env_profile=environ.get("GOVERNANCE_PROFILE"),
    )
    effective = apply_profile_overlay(document, selected)
    validate_structure(effective, version="2")
    validate_semantics_v2(effective)
    return normalize_document_v2(effective, config_path=config_path)


def load_runtime_configuration(
    path: str | Path,
    *,
    profile: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfiguration:
    """Parse governance.yaml once and load the matching schema version."""
    config_path = Path(path)
    document = parse_governance_yaml(config_path)
    env = dict(os.environ if environ is None else environ)
    version = _declared_schema_version(document)
    if version == "1":
        canonical = _load_v1_from_document(
            document,
            config_path,
            profile=profile,
            environ=env,
        )
        return V1RuntimeConfiguration(kind="1", canonical=canonical)
    loaded = _load_v2_from_document(
        document,
        config_path,
        profile=profile,
        environ=env,
    )
    return V2RuntimeConfiguration(kind="2", loaded=loaded)


def resolve_v2_providers(
    runtime: V2RuntimeConfiguration,
    *,
    registry: ProviderRegistry,
    environ: Mapping[str, str] | None = None,
) -> ResolvedProviderConfiguration:
    """Resolve v2 provider bindings against a registry (pre-I/O)."""
    return resolve_provider_configuration(
        runtime.loaded,
        registry=registry,
        environ=environ,
    )


__all__ = [
    "RuntimeConfiguration",
    "V1RuntimeConfiguration",
    "V2RuntimeConfiguration",
    "load_runtime_configuration",
    "resolve_v2_providers",
]
