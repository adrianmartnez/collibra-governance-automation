"""Provider SDK public contract tests (#90)."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

import pytest

from governance.providers import (
    PROVIDER_ENTRY_POINT_GROUP,
    PROVIDER_SDK_API_VERSION,
    SUPPORTED_CAPABILITIES,
    AuthorizedMutationCapability,
    CapabilityBinding,
    CapabilityId,
    CompatibilityPreflightCapability,
    GovernanceGraphCapability,
    LineageCapability,
    MetadataDiscoveryCapability,
    PropertyObservationsCapability,
    ProviderConfigValidator,
    ProviderDescriptor,
    ProviderDescriptorError,
    ProviderRegistration,
    ProviderRegistrationError,
    ProviderRuntimeContext,
    RemoteStateReadCapability,
    TargetPlanningCapability,
)
from governance.providers.contracts import (
    CapabilityT_co,
    DesiredStateT_contra,
    MutationRequestT_contra,
    MutationResultT_co,
    PlanT_co,
    PreflightResultT_co,
    RemoteStateT_co,
    RemoteStateT_contra,
)


def _factory(_context: ProviderRuntimeContext) -> object:
    raise AssertionError("capability factory must not run during contract validation")


def _binding(capability: CapabilityId) -> CapabilityBinding:
    return CapabilityBinding(capability_id=capability, factory=_factory)


def test_public_constants() -> None:
    assert PROVIDER_SDK_API_VERSION == "1"
    assert PROVIDER_ENTRY_POINT_GROUP == "governance.providers"


def test_supported_capabilities_frozen_exact_set() -> None:
    expected = {
        "metadata_discovery",
        "governance_graph",
        "property_observations",
        "lineage",
        "remote_state_read",
        "target_planning",
        "compatibility_preflight",
        "authorized_mutation",
    }
    assert {item.value for item in SUPPORTED_CAPABILITIES} == expected
    assert set(CapabilityId) == SUPPORTED_CAPABILITIES


def test_provider_descriptor_accepts_valid_identity() -> None:
    descriptor = ProviderDescriptor(
        provider_id="acme.snowflake",
        display_name="Acme Snowflake",
        provider_version="1.2.3",
        sdk_compatibility=">=2",
        capabilities=(CapabilityId.LINEAGE,),
    )
    assert descriptor.provider_id == "acme.snowflake"
    assert descriptor.capabilities == (CapabilityId.LINEAGE,)


@pytest.mark.parametrize(
    "provider_id",
    ["ACME.Snowflake", "Acme", "1bad", "bad-id", "Bad.Id", ""],
)
def test_provider_id_rejects_without_normalization(provider_id: str) -> None:
    with pytest.raises(ProviderDescriptorError) as exc_info:
        ProviderDescriptor(
            provider_id=provider_id,
            display_name="X",
            provider_version="1.0.0",
            sdk_compatibility="==1",
            capabilities=(CapabilityId.LINEAGE,),
        )
    assert any(item.code == "invalid_provider_id" for item in exc_info.value.errors)


def test_provider_version_requires_pep440() -> None:
    with pytest.raises(ProviderDescriptorError) as exc_info:
        ProviderDescriptor(
            provider_id="acme.example",
            display_name="X",
            provider_version="not a version",
            sdk_compatibility="==1",
            capabilities=(CapabilityId.LINEAGE,),
        )
    assert any(item.code == "invalid_provider_version" for item in exc_info.value.errors)


def test_sdk_compatibility_malformed_rejected_structurally() -> None:
    with pytest.raises(ProviderDescriptorError) as exc_info:
        ProviderDescriptor(
            provider_id="acme.example",
            display_name="X",
            provider_version="1.0.0",
            sdk_compatibility=">>>",
            capabilities=(CapabilityId.LINEAGE,),
        )
    assert any(item.code == "invalid_sdk_compatibility" for item in exc_info.value.errors)


def test_sdk_compatibility_incompatible_range_is_structurally_valid() -> None:
    descriptor = ProviderDescriptor(
        provider_id="acme.example",
        display_name="X",
        provider_version="1.0.0",
        sdk_compatibility=">=2",
        capabilities=(CapabilityId.LINEAGE,),
    )
    assert descriptor.sdk_compatibility == ">=2"


def test_unknown_and_duplicate_capabilities_rejected() -> None:
    with pytest.raises(ProviderDescriptorError) as exc_info:
        ProviderDescriptor(
            provider_id="acme.example",
            display_name="X",
            provider_version="1.0.0",
            sdk_compatibility="==1",
            capabilities=(CapabilityId.LINEAGE, "not_a_capability", CapabilityId.LINEAGE),
        )
    codes = {item.code for item in exc_info.value.errors}
    assert "unknown_capability" in codes
    assert "invalid_descriptor" in codes


def test_registration_requires_binding_coherence() -> None:
    descriptor = ProviderDescriptor(
        provider_id="acme.example",
        display_name="X",
        provider_version="1.0.0",
        sdk_compatibility="==1",
        capabilities=(CapabilityId.LINEAGE, CapabilityId.GOVERNANCE_GRAPH),
    )
    with pytest.raises(ProviderRegistrationError) as exc_info:
        ProviderRegistration(
            descriptor=descriptor,
            bindings=(_binding(CapabilityId.LINEAGE),),
        )
    assert any(item.code == "invalid_capability_binding" for item in exc_info.value.errors)
    missing_paths = [item.path for item in exc_info.value.errors]
    assert "/bindings/governance_graph" in missing_paths


def test_registration_rejects_undeclared_binding() -> None:
    descriptor = ProviderDescriptor(
        provider_id="acme.example",
        display_name="X",
        provider_version="1.0.0",
        sdk_compatibility="==1",
        capabilities=(CapabilityId.LINEAGE,),
    )
    with pytest.raises(ProviderRegistrationError):
        ProviderRegistration(
            descriptor=descriptor,
            bindings=(
                _binding(CapabilityId.LINEAGE),
                _binding(CapabilityId.GOVERNANCE_GRAPH),
            ),
        )


def test_capability_factory_receives_runtime_context() -> None:
    seen: list[ProviderRuntimeContext] = []

    def factory(context: ProviderRuntimeContext) -> object:
        seen.append(context)
        return object()

    binding = CapabilityBinding(capability_id=CapabilityId.LINEAGE, factory=factory)
    context = ProviderRuntimeContext(config={"path": "x"})
    assert binding.factory(context) is not None
    assert seen[0].config["path"] == "x"


def test_config_validator_boundary() -> None:
    seen: list[Mapping[str, object]] = []

    class _Validator:
        def validate(self, config: Mapping[str, object]) -> None:
            seen.append(config)

    registration = ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="acme.example",
            display_name="X",
            provider_version="1.0.0",
            sdk_compatibility="==1",
            capabilities=(CapabilityId.LINEAGE,),
        ),
        bindings=(_binding(CapabilityId.LINEAGE),),
        config_validator=_Validator(),
    )
    assert registration.config_validator is not None
    registration.config_validator.validate({"option": "value"})
    assert seen == [{"option": "value"}]


def test_config_validator_rejects_non_protocol_shape() -> None:
    with pytest.raises(ProviderRegistrationError) as exc_info:
        ProviderRegistration(
            descriptor=ProviderDescriptor(
                provider_id="acme.example",
                display_name="X",
                provider_version="1.0.0",
                sdk_compatibility="==1",
                capabilities=(CapabilityId.LINEAGE,),
            ),
            bindings=(_binding(CapabilityId.LINEAGE),),
            config_validator=object(),  # type: ignore[arg-type]
        )
    assert any(item.code == "invalid_config_validator" for item in exc_info.value.errors)


def test_capability_protocols_are_importable() -> None:
    assert MetadataDiscoveryCapability is not None
    assert GovernanceGraphCapability is not None
    assert PropertyObservationsCapability is not None
    assert LineageCapability is not None
    assert RemoteStateReadCapability is not None
    assert TargetPlanningCapability is not None
    assert CompatibilityPreflightCapability is not None
    assert AuthorizedMutationCapability is not None
    assert ProviderConfigValidator is not None


def test_target_planning_requires_explicit_remote_state() -> None:
    class _Planner:
        def build_plan(self, desired_state: dict[str, str], remote_state: dict[str, str]) -> str:
            return f"{desired_state['name']}:{remote_state['id']}"

    planner: TargetPlanningCapability[dict[str, str], dict[str, str], str] = _Planner()
    assert planner.build_plan({"name": "desired"}, {"id": "remote-1"}) == "desired:remote-1"

    signature = TargetPlanningCapability.build_plan.__annotations__
    assert "desired_state" in signature
    assert "remote_state" in signature
    assert "return" in signature


def test_public_protocol_typevars_use_correct_variance() -> None:
    assert CapabilityT_co.__covariant__ is True
    assert CapabilityT_co.__contravariant__ is False
    assert RemoteStateT_co.__covariant__ is True
    assert RemoteStateT_contra.__contravariant__ is True
    assert DesiredStateT_contra.__contravariant__ is True
    assert PlanT_co.__covariant__ is True
    assert PreflightResultT_co.__covariant__ is True
    assert MutationRequestT_contra.__contravariant__ is True
    assert MutationResultT_co.__covariant__ is True

    assert RemoteStateReadCapability.__parameters__ == (RemoteStateT_co,)
    assert TargetPlanningCapability.__parameters__ == (
        DesiredStateT_contra,
        RemoteStateT_contra,
        PlanT_co,
    )
    assert CompatibilityPreflightCapability.__parameters__ == (PreflightResultT_co,)
    assert AuthorizedMutationCapability.__parameters__ == (
        MutationRequestT_contra,
        MutationResultT_co,
    )


def test_capability_binding_invalid_id_raises_registration_error() -> None:
    with pytest.raises(ProviderRegistrationError) as exc_info:
        CapabilityBinding(capability_id="not_a_capability", factory=_factory)  # type: ignore[arg-type]
    assert not isinstance(exc_info.value, ProviderDescriptorError)
    assert type(exc_info.value) is ProviderRegistrationError
    assert any(item.code == "unknown_capability" for item in exc_info.value.errors)


def test_providers_package_does_not_import_integrations_or_cli() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "governance" / "providers"
    forbidden = (
        "governance.integrations",
        "governance.cli",
        "governance.reconciliation.sources",
        "governance.reconciliation.targets",
    )
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for item in forbidden:
                        assert not alias.name.startswith(item), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                for item in forbidden:
                    assert not node.module.startswith(item), path
