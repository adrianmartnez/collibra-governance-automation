"""Focused tests for pre-I/O provider resolution and construction (#94)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.config_contract import (
    ConfigResolutionError,
    ConfigSemanticError,
    LoadedConfigV2,
    ProviderConstructionError,
    construct_provider_capability,
    load_canonical_config_v2,
    resolve_provider_configuration,
)
from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderDiagnostic,
    ProviderError,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRegistryError,
    ProviderRuntimeContext,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "governance_yaml_v2"
SECRET = "super-secret-token-value-9f3a"


class _RecordingValidator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail = False

    def validate(self, config: dict[str, object]) -> None:
        self.calls.append(dict(config))
        if self.fail:
            raise ProviderError(
                [
                    ProviderDiagnostic(
                        code="invalid_capability_binding",
                        path="/token",
                        message="token is invalid",
                    )
                ]
            )
        # Must see unresolved $env refs, never secret values.
        assert SECRET not in repr(config)


def _registration(
    provider_id: str,
    *,
    capabilities: tuple[CapabilityId, ...] = (CapabilityId.LINEAGE,),
    validator: _RecordingValidator | None = None,
    factory_calls: list[str] | None = None,
    factory_raises: BaseException | None = None,
    factory_secret: str | None = None,
) -> ProviderRegistration:
    calls = factory_calls if factory_calls is not None else []

    def factory(context: ProviderRuntimeContext) -> object:
        calls.append("factory")
        if factory_raises is not None:
            raise factory_raises
        if factory_secret is not None:
            raise RuntimeError(f"boom with {factory_secret}")
        return {"config": dict(context.config), "config_root": context.config_root}

    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id=provider_id,
            display_name=provider_id,
            provider_version="1.0.0",
            sdk_compatibility="==1",
            capabilities=capabilities,
        ),
        bindings=tuple(
            CapabilityBinding(capability_id=capability, factory=factory)
            for capability in capabilities
        ),
        config_validator=validator,
    )


def test_resolve_happy_path_does_not_invoke_factory() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_with_env.yaml")
    validator = _RecordingValidator()
    factory_calls: list[str] = []
    registry = ProviderRegistry()
    registry.register(
        _registration(
            "example.fixture",
            validator=validator,
            factory_calls=factory_calls,
        )
    )
    resolved = resolve_provider_configuration(
        loaded,
        registry=registry,
        environ={
            "PROVIDER_TOKEN": SECRET,
            "PROVIDER_PASSWORD": "pw",
        },
    )
    assert isinstance(resolved.canonical, type(loaded.canonical))
    assert resolved.canonical is loaded.canonical
    assert factory_calls == []
    assert len(validator.calls) == 1
    assert validator.calls[0]["token"] == {"$env": "PROVIDER_TOKEN"}
    binding = resolved.sources[0]
    assert binding.runtime_context.config["token"] == SECRET
    assert binding.runtime_context.config_root == loaded.canonical.config_root
    assert binding.config_path == "/sources/0"
    assert SECRET not in repr(binding.runtime_context)


def test_shared_logical_id_locations_role_scoped() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_shared_logical_id.yaml")
    registry = ProviderRegistry()
    registry.register(_registration("example.source"))
    registry.register(_registration("example.target"))
    resolved = resolve_provider_configuration(loaded, registry=registry, environ={})
    assert resolved.sources[0].logical_id == "catalog"
    assert resolved.targets[0].logical_id == "catalog"
    assert resolved.sources[0].config_path == "/sources/0"
    assert resolved.targets[0].config_path == "/targets/0"


def test_validator_runs_before_env_resolve() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_with_env.yaml")
    validator = _RecordingValidator()
    validator.fail = True
    registry = ProviderRegistry()
    registry.register(_registration("example.fixture", validator=validator))
    with pytest.raises(ProviderRegistryError) as exc:
        resolve_provider_configuration(
            loaded,
            registry=registry,
            environ={"PROVIDER_TOKEN": SECRET, "PROVIDER_PASSWORD": "pw"},
        )
    assert any(item.path == "/sources/0/config/token" for item in exc.value.errors)
    assert SECRET not in str(exc.value)


def test_unresolved_env_fails_after_validator() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_with_env.yaml")
    validator = _RecordingValidator()
    registry = ProviderRegistry()
    registry.register(_registration("example.fixture", validator=validator))
    with pytest.raises(ConfigResolutionError) as exc:
        resolve_provider_configuration(loaded, registry=registry, environ={})
    assert len(validator.calls) == 1
    assert exc.value.code == "environment_reference_unresolved"
    assert exc.value.path.startswith("/sources/0/config/")


def test_unknown_provider_uses_config_pointer() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_minimal.yaml")
    registry = ProviderRegistry()
    with pytest.raises(ProviderRegistryError) as exc:
        resolve_provider_configuration(loaded, registry=registry, environ={})
    assert any(
        item.code == "unknown_provider" and item.path == "/sources/0/provider"
        for item in exc.value.errors
    )


def test_sdk_incompatibility_fails_at_register_before_resolution() -> None:
    """Incompatible sdk_compatibility is rejected by registry.register / discovery.

    Resolve never sees an incompatible registration; the failure happens before
    provider validation, env resolution, capability factory, and operational I/O.
    """
    validator = _RecordingValidator()
    factory_calls: list[str] = []

    def factory(context: ProviderRuntimeContext) -> object:
        factory_calls.append("factory")
        _ = context
        return object()

    registry = ProviderRegistry()
    incompatible = ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="example.fixture",
            display_name="X",
            provider_version="1.0.0",
            sdk_compatibility=">=2",
            capabilities=(CapabilityId.LINEAGE,),
        ),
        bindings=(CapabilityBinding(capability_id=CapabilityId.LINEAGE, factory=factory),),
        config_validator=validator,
    )
    with pytest.raises(ProviderRegistryError) as exc:
        registry.register(incompatible)
    assert any(item.code == "incompatible_sdk" for item in exc.value.errors)
    assert validator.calls == []
    assert factory_calls == []
    # Registry remains empty — resolve cannot proceed with this provider.
    loaded = load_canonical_config_v2(FIXTURES / "valid_minimal.yaml")
    with pytest.raises(ProviderRegistryError) as resolve_exc:
        resolve_provider_configuration(loaded, registry=registry, environ={})
    assert any(item.code == "unknown_provider" for item in resolve_exc.value.errors)
    assert validator.calls == []
    assert factory_calls == []


def test_construct_invokes_factory_with_resolved_context() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_with_env.yaml")
    registry = ProviderRegistry()
    registry.register(_registration("example.fixture"))
    resolved = resolve_provider_configuration(
        loaded,
        registry=registry,
        environ={"PROVIDER_TOKEN": SECRET, "PROVIDER_PASSWORD": "pw"},
    )
    capability = construct_provider_capability(resolved.sources[0], CapabilityId.LINEAGE)
    assert isinstance(capability, dict)
    assert capability["config"]["token"] == SECRET
    assert capability["config_root"] == loaded.canonical.config_root


def test_construct_factory_failure_is_secret_safe() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_minimal.yaml")
    registry = ProviderRegistry()
    registry.register(_registration("example.fixture", factory_secret=SECRET))
    resolved = resolve_provider_configuration(loaded, registry=registry, environ={})
    with pytest.raises(ProviderConstructionError) as exc:
        construct_provider_capability(resolved.sources[0], CapabilityId.LINEAGE)
    text = " ".join(item.message for item in exc.value.errors)
    assert SECRET not in text
    assert "RuntimeError" in text
    assert exc.value.__cause__ is None
    assert SECRET not in repr(exc.value)


def test_resolve_requires_loaded_config_v2() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ConfigSemanticError):
        resolve_provider_configuration(object(), registry=registry)  # type: ignore[arg-type]


def test_loaded_atomic_boundary() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_minimal.yaml")
    assert isinstance(loaded, LoadedConfigV2)
    assert loaded.locations.source_locations["primary"] == "/sources/0"


def test_absent_env_var_is_unresolved() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_with_env.yaml")
    registry = ProviderRegistry()
    registry.register(_registration("example.fixture"))
    with pytest.raises(ConfigResolutionError) as exc:
        resolve_provider_configuration(
            loaded,
            registry=registry,
            environ={"PROVIDER_PASSWORD": "pw"},
        )
    assert exc.value.code == "environment_reference_unresolved"
    assert "PROVIDER_TOKEN" in str(exc.value)


def test_empty_env_var_resolves_to_empty_string() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_with_env.yaml")
    registry = ProviderRegistry()
    registry.register(_registration("example.fixture"))
    resolved = resolve_provider_configuration(
        loaded,
        registry=registry,
        environ={"PROVIDER_TOKEN": "", "PROVIDER_PASSWORD": "pw"},
    )
    assert resolved.sources[0].runtime_context.config["token"] == ""
    assert resolved.sources[0].runtime_context.config["nested"]["password"] == "pw"


def test_factory_may_reject_empty_resolved_env_secret_safe() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_with_env.yaml")

    def factory(context: ProviderRuntimeContext) -> object:
        token = context.config.get("token")
        if token == "":
            raise RuntimeError(f"token empty but secret was {SECRET}")
        return object()

    registry = ProviderRegistry()
    registry.register(
        ProviderRegistration(
            descriptor=ProviderDescriptor(
                provider_id="example.fixture",
                display_name="example.fixture",
                provider_version="1.0.0",
                sdk_compatibility="==1",
                capabilities=(CapabilityId.LINEAGE,),
            ),
            bindings=(CapabilityBinding(capability_id=CapabilityId.LINEAGE, factory=factory),),
        )
    )
    resolved = resolve_provider_configuration(
        loaded,
        registry=registry,
        environ={"PROVIDER_TOKEN": "", "PROVIDER_PASSWORD": "pw"},
    )
    assert resolved.sources[0].runtime_context.config["token"] == ""
    with pytest.raises(ProviderConstructionError) as exc:
        construct_provider_capability(resolved.sources[0], CapabilityId.LINEAGE)
    text = " ".join(item.message for item in exc.value.errors)
    assert SECRET not in text
    assert "RuntimeError" in text
    assert exc.value.__cause__ is None


def test_runtime_context_default_config_root_is_none() -> None:
    context = ProviderRuntimeContext(config={"x": 1})
    assert context.config_root is None


def test_v2_resolution_sets_absolute_config_root() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_minimal.yaml")
    registry = ProviderRegistry()
    registry.register(_registration("example.fixture"))
    resolved = resolve_provider_configuration(loaded, registry=registry, environ={})
    root = resolved.sources[0].runtime_context.config_root
    assert root is not None
    assert root == loaded.canonical.config_root
    assert Path(root).is_absolute()


def test_rfc6901_escaped_keys_in_env_resolution_diagnostics() -> None:
    loaded = load_canonical_config_v2(FIXTURES / "valid_pointer_keys.yaml")
    registry = ProviderRegistry()
    registry.register(_registration("example.fixture"))
    with pytest.raises(ConfigResolutionError) as exc:
        resolve_provider_configuration(loaded, registry=registry, environ={})
    assert exc.value.path in {
        "/sources/0/config/foo~1bar",
        "/sources/0/config/foo~0bar",
    }
    # Unescaped slash must not appear as a path segment separator for the key.
    assert exc.value.path != "/sources/0/config/foo/bar"
    assert not exc.value.path.endswith("/foo~bar")

    resolved = resolve_provider_configuration(
        loaded,
        registry=registry,
        environ={"TOKEN_A": "a", "TOKEN_B": "b"},
    )
    config = resolved.sources[0].runtime_context.config
    assert config["foo/bar"] == "a"
    assert config["foo~bar"] == "b"
