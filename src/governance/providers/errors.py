"""Public Provider SDK errors and deterministic diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

CODE_INVALID_DESCRIPTOR = "invalid_descriptor"
CODE_INVALID_PROVIDER_ID = "invalid_provider_id"
CODE_DUPLICATE_PROVIDER_ID = "duplicate_provider_id"
CODE_UNKNOWN_PROVIDER = "unknown_provider"
CODE_INCOMPATIBLE_SDK = "incompatible_sdk"
CODE_UNKNOWN_CAPABILITY = "unknown_capability"
CODE_MISSING_CAPABILITY = "missing_capability"
CODE_INVALID_CAPABILITY_BINDING = "invalid_capability_binding"
CODE_BROKEN_ENTRY_POINT = "broken_entry_point"
CODE_DISCOVERY_FAILED = "discovery_failed"
CODE_INVALID_PROVIDER_VERSION = "invalid_provider_version"
CODE_INVALID_SDK_COMPATIBILITY = "invalid_sdk_compatibility"
CODE_INVALID_CONFIG_VALIDATOR = "invalid_config_validator"


@dataclass(frozen=True, slots=True)
class ProviderDiagnostic:
    """Secret-safe, deterministic provider diagnostic."""

    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def sort_diagnostics(
    errors: list[ProviderDiagnostic] | tuple[ProviderDiagnostic, ...],
) -> tuple[ProviderDiagnostic, ...]:
    return tuple(sorted(errors, key=lambda item: (item.path, item.code, item.message)))


class ProviderError(Exception):
    """Base public Provider SDK error with ordered diagnostics."""

    def __init__(
        self,
        errors: list[ProviderDiagnostic] | tuple[ProviderDiagnostic, ...],
    ) -> None:
        self.errors = sort_diagnostics(errors)
        super().__init__(self.errors[0].message if self.errors else "provider error")


class ProviderDescriptorError(ProviderError):
    """Structural descriptor validation failed."""


class ProviderRegistrationError(ProviderError):
    """Internal registration coherence validation failed."""


class ProviderRegistryError(ProviderError):
    """Registry registration, lookup, or negotiation failed."""


class ProviderDiscoveryError(ProviderError):
    """Entry-point discovery failed closed."""
