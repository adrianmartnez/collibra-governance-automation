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
from governance.config_contract.resolution_diagnostics import (
    RESOLUTION_DIAGNOSTIC_SCHEMA,
    RESOLUTION_DIAGNOSTIC_VERSION,
    ResolutionDiagnosticError,
    resolution_diagnostics_failure,
    runtime_invalid_diagnostic,
    unresolved_env_diagnostic,
)
from governance.config_contract.resolve import (
    ConfigResolutionError,
    resolve_inventory_path,
    resolve_mapping_path,
    resolve_settings,
    resolve_snapshot_path,
    validate_collibra_runtime,
)

__all__ = [
    "CanonicalConfig",
    "ConfigContractError",
    "ConfigParseError",
    "ConfigResolutionError",
    "ConfigSchemaError",
    "ConfigSemanticError",
    "RESOLUTION_DIAGNOSTIC_SCHEMA",
    "RESOLUTION_DIAGNOSTIC_VERSION",
    "ResolutionDiagnosticError",
    "UnsupportedConfigVersionError",
    "diagnostics_failure",
    "diagnostics_success",
    "load_canonical_config",
    "resolution_diagnostics_failure",
    "resolve_inventory_path",
    "resolve_mapping_path",
    "resolve_settings",
    "resolve_snapshot_path",
    "runtime_invalid_diagnostic",
    "unresolved_env_diagnostic",
    "validate_collibra_runtime",
    "validate_governance_config",
]
