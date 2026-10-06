"""Provider registry and capability negotiation tests (#91)."""

from __future__ import annotations

import pytest

from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRegistryError,
    ProviderRuntimeContext,
)


def _tracking_factory(calls: list[str]):
    def factory(context: ProviderRuntimeContext) -> object:
        calls.append("called")
        _ = context
        return object()

    return factory


def _registration(
    provider_id: str,
    *,
    sdk_compatibility: str = "==1",
    capabilities: tuple[CapabilityId, ...] = (CapabilityId.LINEAGE,),
    calls: list[str] | None = None,
) -> ProviderRegistration:
    tracked = calls if calls is not None else []
    factory = _tracking_factory(tracked)
    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id=provider_id,
            display_name=provider_id,
            provider_version="1.0.0",
            sdk_compatibility=sdk_compatibility,
            capabilities=capabilities,
        ),
        bindings=tuple(
            CapabilityBinding(capability_id=capability, factory=factory)
            for capability in capabilities
        ),
    )


def test_register_lookup_and_unknown() -> None:
    registry = ProviderRegistry()
    registry.register(_registration("acme.one"))
    assert registry.get("acme.one").descriptor.provider_id == "acme.one"
    with pytest.raises(ProviderRegistryError) as exc_info:
        registry.get("missing.provider")
    assert any(item.code == "unknown_provider" for item in exc_info.value.errors)


def test_duplicate_provider_id_is_hard_error() -> None:
    registry = ProviderRegistry()
    registry.register(_registration("acme.one"))
    with pytest.raises(ProviderRegistryError) as exc_info:
        registry.register(_registration("acme.one"))
    assert any(item.code == "duplicate_provider_id" for item in exc_info.value.errors)


def test_enumeration_is_lexicographic_and_order_independent() -> None:
    first = ProviderRegistry()
    first.register(_registration("zeta.provider"))
    first.register(_registration("alpha.provider"))
    first.register(_registration("middle.provider"))

    second = ProviderRegistry()
    second.register(_registration("middle.provider"))
    second.register(_registration("alpha.provider"))
    second.register(_registration("zeta.provider"))

    assert (
        first.provider_ids()
        == second.provider_ids()
        == (
            "alpha.provider",
            "middle.provider",
            "zeta.provider",
        )
    )


@pytest.mark.parametrize("specifier", ["==1", ">=1,<2", ">=1"])
def test_compatible_sdk_specifiers(specifier: str) -> None:
    registry = ProviderRegistry()
    registry.register(_registration("acme.ok", sdk_compatibility=specifier))
    assert "acme.ok" in registry


@pytest.mark.parametrize("specifier", ["<1", ">=2", "==2"])
def test_incompatible_sdk_fails_at_registry(specifier: str) -> None:
    # Descriptor construction must succeed for syntactically valid incompatible ranges.
    registration = _registration("acme.bad", sdk_compatibility=specifier)
    registry = ProviderRegistry()
    with pytest.raises(ProviderRegistryError) as exc_info:
        registry.register(registration)
    assert any(item.code == "incompatible_sdk" for item in exc_info.value.errors)


def test_require_capabilities_explicit_and_ordered_diagnostics() -> None:
    registry = ProviderRegistry()
    registry.register(
        _registration(
            "acme.source",
            capabilities=(CapabilityId.LINEAGE, CapabilityId.GOVERNANCE_GRAPH),
        )
    )
    registration = registry.require_capabilities(
        "acme.source",
        (CapabilityId.LINEAGE, CapabilityId.GOVERNANCE_GRAPH),
    )
    assert registration.descriptor.provider_id == "acme.source"

    with pytest.raises(ProviderRegistryError) as exc_info:
        registry.require_capabilities(
            "acme.source",
            (
                CapabilityId.PROPERTY_OBSERVATIONS,
                CapabilityId.METADATA_DISCOVERY,
                CapabilityId.LINEAGE,
            ),
        )
    missing = [item for item in exc_info.value.errors if item.code == "missing_capability"]
    assert [item.path for item in missing] == [
        "/providers/acme.source/capabilities/metadata_discovery",
        "/providers/acme.source/capabilities/property_observations",
    ]


def test_require_unknown_capability_id_fails() -> None:
    registry = ProviderRegistry()
    registry.register(_registration("acme.source"))
    with pytest.raises(ProviderRegistryError) as exc_info:
        registry.require_capabilities("acme.source", ("not_real",))
    assert any(item.code == "unknown_capability" for item in exc_info.value.errors)


def test_factories_never_invoked_during_register_or_negotiation() -> None:
    calls: list[str] = []
    registry = ProviderRegistry()
    registry.register(
        _registration(
            "acme.source",
            capabilities=(CapabilityId.LINEAGE, CapabilityId.GOVERNANCE_GRAPH),
            calls=calls,
        )
    )
    registry.require_capabilities(
        "acme.source",
        (CapabilityId.LINEAGE, CapabilityId.GOVERNANCE_GRAPH),
    )
    _ = registry.list_providers()
    assert calls == []
