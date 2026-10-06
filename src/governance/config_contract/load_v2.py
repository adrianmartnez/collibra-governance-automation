"""Load and validate governance.yaml v2 into LoadedConfigV2."""

from __future__ import annotations

import os
from pathlib import Path

from governance.config_contract.models_v2 import LoadedConfigV2
from governance.config_contract.normalize_v2 import normalize_document_v2
from governance.config_contract.parse import parse_governance_yaml
from governance.config_contract.profiles import apply_profile_overlay, select_profile_name
from governance.config_contract.schema import validate_structure
from governance.config_contract.semantic_v2 import validate_semantics_v2
from governance.identity import config_identity_v2


def load_canonical_config_v2(
    path: str | Path,
    *,
    profile: str | None = None,
    environ: dict[str, str] | None = None,
) -> LoadedConfigV2:
    """Parse, validate, overlay profile, and normalize governance.yaml v2."""
    config_path = Path(path)
    document = parse_governance_yaml(config_path)
    validate_structure(document, version="2")

    env = environ if environ is not None else os.environ
    selected = select_profile_name(
        cli_profile=profile,
        env_profile=env.get("GOVERNANCE_PROFILE"),
    )
    effective = apply_profile_overlay(document, selected)
    validate_structure(effective, version="2")
    validate_semantics_v2(effective)
    return normalize_document_v2(effective, config_path=config_path)


def validate_governance_config_v2(
    path: str | Path,
    *,
    profile: str | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[LoadedConfigV2, dict[str, str]]:
    """Validate v2 config and return (loaded, config_identity_v2.to_dict())."""
    loaded = load_canonical_config_v2(path, profile=profile, environ=environ)
    identity = config_identity_v2(loaded.canonical.identity_projection()).to_dict()
    return loaded, identity
