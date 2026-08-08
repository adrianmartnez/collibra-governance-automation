"""Governance-as-Code configuration contract."""

from governance.config_contract.errors import (
    ConfigContractError,
    ConfigParseError,
    ConfigSchemaError,
    ConfigSemanticError,
    UnsupportedConfigVersionError,
    diagnostics_failure,
    diagnostics_success,
)
from governance.config_contract.load import load_canonical_config, validate_governance_config
from governance.config_contract.models import CanonicalConfig
from governance.config_contract.resolve import (
    ConfigResolutionError,
    resolve_inventory_path,
    resolve_mapping_path,
    resolve_settings,
    resolve_snapshot_path,
)

__all__ = [
    "CanonicalConfig",
    "ConfigContractError",
    "ConfigParseError",
    "ConfigResolutionError",
    "ConfigSchemaError",
    "ConfigSemanticError",
    "UnsupportedConfigVersionError",
    "diagnostics_failure",
    "diagnostics_success",
    "load_canonical_config",
    "resolve_inventory_path",
    "resolve_mapping_path",
    "resolve_settings",
    "resolve_snapshot_path",
    "validate_governance_config",
]
