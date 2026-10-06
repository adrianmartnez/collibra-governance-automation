"""Entry-point registration for the example fixture provider."""

from __future__ import annotations

from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRuntimeContext,
)


class _FixtureLineageCapability:
    def load_lineage(self) -> tuple[object, ...]:
        return ()


def _lineage_factory(context: ProviderRuntimeContext) -> _FixtureLineageCapability:
    # Runtime construction only; discovery/registration must never call this.
    _ = context
    return _FixtureLineageCapability()


def register() -> ProviderRegistration:
    """Zero-argument entry-point callable required by governance.providers."""
    descriptor = ProviderDescriptor(
        provider_id="example.fixture",
        display_name="Example Fixture Provider",
        provider_version="0.1.0",
        sdk_compatibility=">=1,<2",
        capabilities=(CapabilityId.LINEAGE,),
    )
    return ProviderRegistration(
        descriptor=descriptor,
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.LINEAGE,
                factory=_lineage_factory,
            ),
        ),
    )
