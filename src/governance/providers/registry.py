"""Deterministic provider registry and explicit capability negotiation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from packaging.specifiers import SpecifierSet

from governance.providers.capabilities import (
    CapabilityId,
    parse_capability_id,
    sorted_capability_values,
)
from governance.providers.contracts import (
    PROVIDER_SDK_API_VERSION,
    ProviderRegistration,
)
from governance.providers.errors import (
    CODE_DUPLICATE_PROVIDER_ID,
    CODE_INCOMPATIBLE_SDK,
    CODE_MISSING_CAPABILITY,
    CODE_UNKNOWN_CAPABILITY,
    CODE_UNKNOWN_PROVIDER,
    ProviderDescriptorError,
    ProviderDiagnostic,
    ProviderRegistryError,
)


class ProviderRegistry:
    """Explicit in-memory registry. Enumeration is always sorted by provider_id."""

    def __init__(self) -> None:
        self._registrations: dict[str, ProviderRegistration] = {}

    def register(self, registration: ProviderRegistration) -> None:
        if not isinstance(registration, ProviderRegistration):
            raise ProviderRegistryError(
                [
                    ProviderDiagnostic(
                        code=CODE_UNKNOWN_PROVIDER,
                        path="/registration",
                        message="registration must be a ProviderRegistration",
                    )
                ]
            )

        provider_id = registration.descriptor.provider_id
        self._assert_sdk_compatible(registration)

        if provider_id in self._registrations:
            raise ProviderRegistryError(
                [
                    ProviderDiagnostic(
                        code=CODE_DUPLICATE_PROVIDER_ID,
                        path=f"/providers/{provider_id}",
                        message=(
                            f"duplicate provider_id {provider_id!r}; "
                            "installation or registration order must not decide precedence"
                        ),
                    )
                ]
            )

        self._registrations[provider_id] = registration

    def get(self, provider_id: str) -> ProviderRegistration:
        if not isinstance(provider_id, str) or not provider_id:
            raise ProviderRegistryError(
                [
                    ProviderDiagnostic(
                        code=CODE_UNKNOWN_PROVIDER,
                        path="/provider_id",
                        message="provider_id must be a non-empty string",
                    )
                ]
            )
        try:
            return self._registrations[provider_id]
        except KeyError as exc:
            raise ProviderRegistryError(
                [
                    ProviderDiagnostic(
                        code=CODE_UNKNOWN_PROVIDER,
                        path=f"/providers/{provider_id}",
                        message=f"unknown provider_id {provider_id!r}",
                    )
                ]
            ) from exc

    def list_providers(self) -> tuple[ProviderRegistration, ...]:
        return tuple(
            self._registrations[provider_id] for provider_id in sorted(self._registrations)
        )

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def require_capabilities(
        self,
        provider_id: str,
        required_capabilities: Sequence[CapabilityId | str],
    ) -> ProviderRegistration:
        registration = self.get(provider_id)
        required = self._parse_required(required_capabilities)
        declared = frozenset(registration.descriptor.capabilities)
        missing = required - declared
        if missing:
            missing_values = sorted_capability_values(missing)
            raise ProviderRegistryError(
                [
                    ProviderDiagnostic(
                        code=CODE_MISSING_CAPABILITY,
                        path=f"/providers/{provider_id}/capabilities/{value}",
                        message=(f"provider {provider_id!r} missing required capability {value!r}"),
                    )
                    for value in missing_values
                ]
            )
        return registration

    def __len__(self) -> int:
        return len(self._registrations)

    def __contains__(self, provider_id: object) -> bool:
        return isinstance(provider_id, str) and provider_id in self._registrations

    def __iter__(self) -> Iterable[ProviderRegistration]:
        return iter(self.list_providers())

    @staticmethod
    def _assert_sdk_compatible(registration: ProviderRegistration) -> None:
        specifier = SpecifierSet(registration.descriptor.sdk_compatibility)
        if PROVIDER_SDK_API_VERSION not in specifier:
            provider_id = registration.descriptor.provider_id
            raise ProviderRegistryError(
                [
                    ProviderDiagnostic(
                        code=CODE_INCOMPATIBLE_SDK,
                        path=f"/providers/{provider_id}/sdk_compatibility",
                        message=(
                            f"provider {provider_id!r} sdk_compatibility "
                            f"{registration.descriptor.sdk_compatibility!r} does not include "
                            f"Provider SDK API version {PROVIDER_SDK_API_VERSION!r}"
                        ),
                    )
                ]
            )

    @staticmethod
    def _parse_required(
        required_capabilities: Sequence[CapabilityId | str],
    ) -> frozenset[CapabilityId]:
        if not isinstance(required_capabilities, Sequence) or isinstance(
            required_capabilities, (str, bytes)
        ):
            raise ProviderRegistryError(
                [
                    ProviderDiagnostic(
                        code=CODE_UNKNOWN_CAPABILITY,
                        path="/required_capabilities",
                        message="required_capabilities must be a sequence of capability ids",
                    )
                ]
            )

        parsed: set[CapabilityId] = set()
        diagnostics: list[ProviderDiagnostic] = []
        for index, raw in enumerate(required_capabilities):
            path = f"/required_capabilities/{index}"
            try:
                capability = parse_capability_id(raw, path=path)
            except ProviderDescriptorError as exc:
                diagnostics.extend(exc.errors)
                continue
            parsed.add(capability)

        if diagnostics:
            raise ProviderRegistryError(diagnostics)
        return frozenset(parsed)
