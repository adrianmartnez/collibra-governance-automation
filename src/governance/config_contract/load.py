"""Load and validate governance.yaml into CanonicalConfig."""

from __future__ import annotations

import os
from pathlib import Path

from governance.config_contract.models import CanonicalConfig
from governance.config_contract.normalize import normalize_document
from governance.config_contract.parse import parse_governance_yaml
from governance.config_contract.profiles import apply_profile_overlay, select_profile_name
from governance.config_contract.schema import validate_structure
from governance.config_contract.semantic import validate_semantics
from governance.identity import config_identity


def load_canonical_config(
    path: str | Path,
    *,
    profile: str | None = None,
    environ: dict[str, str] | None = None,
) -> CanonicalConfig:
    """Parse, validate, overlay profile, and normalize governance.yaml."""
    config_path = Path(path)
    document = parse_governance_yaml(config_path)
    validate_structure(document)

    env = environ if environ is not None else os.environ
    selected = select_profile_name(
        cli_profile=profile,
        env_profile=env.get("GOVERNANCE_PROFILE"),
    )
    effective = apply_profile_overlay(document, selected)
    # Re-validate structure after overlay so invalid overlays fail closed.
    validate_structure(effective)
    validate_semantics(effective)
    return normalize_document(effective, config_path=config_path)


def validate_governance_config(
    path: str | Path,
    *,
    profile: str | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[CanonicalConfig, dict[str, str]]:
    """Validate config and return (canonical, config_identity.to_dict())."""
    canonical = load_canonical_config(path, profile=profile, environ=environ)
    identity = config_identity(canonical.identity_projection()).to_dict()
    return canonical, identity
