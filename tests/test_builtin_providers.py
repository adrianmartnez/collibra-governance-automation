"""Built-in provider registry and config validator tests (#95)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from governance import __version__ as package_version
from governance.config_contract.provider_resolution import construct_provider_capability
from governance.integrations.collibra.mapping import mock_mapping_config
from governance.integrations.odcs import load_odcs_graph
from governance.orchestration.registry import build_provider_registry
from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderError,
    ProviderRegistration,
    ProviderRegistryError,
    ProviderRuntimeContext,
)
from governance.providers.builtins.collibra import register as register_collibra
from governance.providers.builtins.dbt import register as register_dbt
from governance.providers.builtins.odcs import register as register_odcs
from governance.providers.builtins.openlineage import register as register_openlineage
from governance.providers.builtins.postgresql import register as register_postgresql

NS = "acme.commerce"

_BUILTIN_CAPABILITIES: dict[str, frozenset[CapabilityId]] = {
    "collibra": frozenset(
        {
            CapabilityId.REMOTE_STATE_READ,
            CapabilityId.TARGET_PLANNING,
            CapabilityId.COMPATIBILITY_PREFLIGHT,
            CapabilityId.AUTHORIZED_MUTATION,
        }
    ),
    "dbt": frozenset({CapabilityId.GOVERNANCE_GRAPH, CapabilityId.PROPERTY_OBSERVATIONS}),
    "odcs": frozenset({CapabilityId.GOVERNANCE_GRAPH, CapabilityId.PROPERTY_OBSERVATIONS}),
    "openlineage": frozenset(
        {
            CapabilityId.GOVERNANCE_GRAPH,
            CapabilityId.PROPERTY_OBSERVATIONS,
            CapabilityId.LINEAGE,
        }
    ),
    "postgresql": frozenset({CapabilityId.METADATA_DISCOVERY}),
}


def _minimal_odcs_doc() -> dict[str, object]:
    return {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": "contract-orders",
        "version": "1.0.0",
        "status": "active",
        "name": "Orders Contract",
    }


def test_build_provider_registry_builtin_matrix() -> None:
    registry = build_provider_registry(discover_external=False)
    assert registry.provider_ids() == ("collibra", "dbt", "odcs", "openlineage", "postgresql")
    for provider_id, expected in _BUILTIN_CAPABILITIES.items():
        registration = registry.get(provider_id)
        assert frozenset(registration.descriptor.capabilities) == expected


def test_builtin_provider_versions_match_package() -> None:
    registry = build_provider_registry(discover_external=False)
    for provider_id in _BUILTIN_CAPABILITIES:
        assert registry.get(provider_id).descriptor.provider_version == package_version


def test_duplicate_external_postgresql_id_is_hard_error() -> None:
    registry = build_provider_registry(discover_external=False)
    duplicate = ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="postgresql",
            display_name="Duplicate PostgreSQL",
            provider_version="9.9.9",
            sdk_compatibility="==1",
            capabilities=(CapabilityId.METADATA_DISCOVERY,),
        ),
        bindings=(
            CapabilityBinding(
                capability_id=CapabilityId.METADATA_DISCOVERY,
                factory=lambda _ctx: object(),
            ),
        ),
    )
    with pytest.raises(ProviderRegistryError) as exc_info:
        registry.register(duplicate)
    assert any(item.code == "duplicate_provider_id" for item in exc_info.value.errors)


def test_build_registry_raises_on_invalid_external_without_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _factory(_context: ProviderRuntimeContext) -> object:
        return object()

    def _duplicate_postgresql() -> ProviderRegistration:
        return ProviderRegistration(
            descriptor=ProviderDescriptor(
                provider_id="postgresql",
                display_name="External duplicate",
                provider_version="0.0.1",
                sdk_compatibility="==1",
                capabilities=(CapabilityId.METADATA_DISCOVERY,),
            ),
            bindings=(
                CapabilityBinding(
                    capability_id=CapabilityId.METADATA_DISCOVERY,
                    factory=_factory,
                ),
            ),
        )

    monkeypatch.setattr(
        "governance.orchestration.registry.discover_provider_registrations",
        lambda **_: (_duplicate_postgresql(),),
    )
    with pytest.raises(ProviderRegistryError):
        build_provider_registry(discover_external=True)


def test_postgresql_plaintext_password_rejected_pre_io() -> None:
    registration = register_postgresql()
    assert registration.config_validator is not None
    with pytest.raises(ProviderError) as exc_info:
        registration.config_validator.validate(
            {
                "source_name": "demo",
                "host": "localhost",
                "database": "db",
                "user": "u",
                "password": "plaintext-secret",
            }
        )
    assert any(item.path == "/password" for item in exc_info.value.errors)


def test_collibra_secrets_and_mapping_validation() -> None:
    registration = register_collibra()
    assert registration.config_validator is not None
    mapping = mock_mapping_config().to_identity_dict()

    with pytest.raises(ProviderError) as exc_info:
        registration.config_validator.validate(
            {
                "mode": "mock",
                "mapping": mapping,
                "bearer_token": "plain-token",
            }
        )
    assert any(item.path == "/bearer_token" for item in exc_info.value.errors)

    with pytest.raises(ProviderError) as exc_info:
        registration.config_validator.validate(
            {
                "mode": "mock",
                "mapping_path": "mapping.json",
            }
        )
    assert any(item.path == "/mapping_path" for item in exc_info.value.errors)

    with pytest.raises(ProviderError) as exc_info:
        registration.config_validator.validate({"mode": "mock"})
    assert any(item.path == "/mapping" for item in exc_info.value.errors)


@pytest.mark.parametrize(
    ("register", "provider_id"),
    [
        (register_odcs, "odcs"),
        (register_dbt, "dbt"),
        (register_openlineage, "openlineage"),
    ],
)
@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "../escape.yaml",
        {"$env": "ODCS_PATH"},
    ],
)
def test_file_providers_reject_bad_paths(
    register,
    provider_id: str,
    bad_path: object,
) -> None:
    registration = register()
    assert registration.config_validator is not None
    with pytest.raises(ProviderError):
        registration.config_validator.validate(
            {"path": bad_path, "namespace": NS},
        )


def test_lineage_capability_matrix() -> None:
    registry = build_provider_registry(discover_external=False)
    assert CapabilityId.LINEAGE in registry.get("openlineage").descriptor.capabilities
    assert CapabilityId.LINEAGE not in registry.get("odcs").descriptor.capabilities
    assert CapabilityId.LINEAGE not in registry.get("dbt").descriptor.capabilities


def test_odcs_provider_graph_matches_load_odcs_graph(tmp_path: Path) -> None:
    doc_path = tmp_path / "contract.odcs.yaml"
    doc_path.write_text(yaml.safe_dump(_minimal_odcs_doc(), sort_keys=False), encoding="utf-8")

    registry = build_provider_registry(discover_external=False)
    registration = registry.get("odcs")
    binding = type(
        "Binding",
        (),
        {
            "registration": registration,
            "runtime_context": ProviderRuntimeContext(
                config={"path": str(doc_path), "namespace": NS},
                config_root=None,
            ),
        },
    )()

    from governance.config_contract.provider_resolution import ResolvedProviderBinding

    resolved = ResolvedProviderBinding(
        role="source",
        logical_id="odcs-0",
        provider_id="odcs",
        registration=registration,
        runtime_context=binding.runtime_context,
        config_path="/test/odcs",
    )
    capability = construct_provider_capability(resolved, CapabilityId.GOVERNANCE_GRAPH)
    direct = load_odcs_graph(doc_path, namespace=NS)
    assert capability.load_graph().content_identity() == direct.content_identity()


def test_collibra_inline_mapping_without_filesystem() -> None:
    mapping = mock_mapping_config()
    registration = register_collibra()
    context = ProviderRuntimeContext(
        config={"mode": "mock", "mapping": mapping.to_identity_dict()},
        config_root=None,
    )
    capability = registration.bindings[0].factory(context)
    runtime = capability._runtime  # noqa: SLF001 — provider runtime under test
    assert runtime.mapping_config == mapping
