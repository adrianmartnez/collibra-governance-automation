"""Provider entry-point discovery tests (#92)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from governance.providers import (
    CapabilityBinding,
    CapabilityId,
    ProviderDescriptor,
    ProviderDiscoveryError,
    ProviderRegistration,
    ProviderRuntimeContext,
    discover_providers,
)


def _factory(context: ProviderRuntimeContext) -> object:
    raise AssertionError(f"factory must not run during discovery: {context!r}")


def _registration(
    provider_id: str,
    *,
    sdk_compatibility: str = "==1",
) -> ProviderRegistration:
    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id=provider_id,
            display_name=provider_id,
            provider_version="1.0.0",
            sdk_compatibility=sdk_compatibility,
            capabilities=(CapabilityId.LINEAGE,),
        ),
        bindings=(CapabilityBinding(capability_id=CapabilityId.LINEAGE, factory=_factory),),
    )


@dataclass
class FakeEntryPoint:
    name: str
    value: str
    dist_name: str
    dist_version: str
    loader: object

    @property
    def dist(self) -> SimpleNamespace:
        return SimpleNamespace(name=self.dist_name, version=self.dist_version)

    def load(self) -> object:
        if isinstance(self.loader, BaseException):
            raise self.loader
        return self.loader


def test_discover_valid_entry_points_sorted_by_provider_id() -> None:
    eps = [
        FakeEntryPoint(
            name="z",
            value="pkg.z:register",
            dist_name="z-dist",
            dist_version="1",
            loader=lambda: _registration("zeta.provider"),
        ),
        FakeEntryPoint(
            name="a",
            value="pkg.a:register",
            dist_name="a-dist",
            dist_version="1",
            loader=lambda: _registration("alpha.provider"),
        ),
    ]
    # Pass in reverse distribution sort order; discovery must still yield provider_id order.
    registry = discover_providers(entry_points=list(reversed(eps)))
    assert registry.provider_ids() == ("alpha.provider", "zeta.provider")


def test_discovery_order_independent_of_input_sequence() -> None:
    def make(provider_id: str, dist: str) -> FakeEntryPoint:
        return FakeEntryPoint(
            name=provider_id,
            value=f"{dist}:register",
            dist_name=dist,
            dist_version="1",
            loader=lambda pid=provider_id: _registration(pid),
        )

    left = discover_providers(entry_points=[make("b.provider", "b"), make("a.provider", "a")])
    right = discover_providers(entry_points=[make("a.provider", "a"), make("b.provider", "b")])
    assert left.provider_ids() == right.provider_ids() == ("a.provider", "b.provider")


def test_broken_import_is_fail_closed_and_secret_safe() -> None:
    secret = "super-secret-token-value"
    eps = [
        FakeEntryPoint(
            name="broken",
            value="pkg.broken:register",
            dist_name="broken-dist",
            dist_version="1",
            loader=RuntimeError(secret),
        )
    ]
    with pytest.raises(ProviderDiscoveryError) as exc_info:
        discover_providers(entry_points=eps)
    text = " ".join(item.message for item in exc_info.value.errors)
    assert secret not in text
    assert "RuntimeError" in text
    assert any(item.code == "broken_entry_point" for item in exc_info.value.errors)
    assert any(item.code == "discovery_failed" for item in exc_info.value.errors)


def test_non_callable_and_wrong_return_type() -> None:
    eps = [
        FakeEntryPoint(
            name="obj",
            value="pkg.obj:register",
            dist_name="obj-dist",
            dist_version="1",
            loader=object(),
        )
    ]
    with pytest.raises(ProviderDiscoveryError):
        discover_providers(entry_points=eps)

    eps = [
        FakeEntryPoint(
            name="bad",
            value="pkg.bad:register",
            dist_name="bad-dist",
            dist_version="1",
            loader=lambda: "not-a-registration",
        )
    ]
    with pytest.raises(ProviderDiscoveryError) as exc_info:
        discover_providers(entry_points=eps)
    assert any("ProviderRegistration" in item.message for item in exc_info.value.errors)


def test_duplicate_provider_id_across_entry_points() -> None:
    eps = [
        FakeEntryPoint(
            name="one",
            value="pkg.one:register",
            dist_name="one-dist",
            dist_version="1",
            loader=lambda: _registration("dup.provider"),
        ),
        FakeEntryPoint(
            name="two",
            value="pkg.two:register",
            dist_name="two-dist",
            dist_version="1",
            loader=lambda: _registration("dup.provider"),
        ),
    ]
    with pytest.raises(ProviderDiscoveryError) as exc_info:
        discover_providers(entry_points=eps)
    assert any(item.code == "duplicate_provider_id" for item in exc_info.value.errors)


def test_incompatible_sdk_fails_before_factory() -> None:
    calls: list[str] = []

    def factory(context: ProviderRuntimeContext) -> object:
        calls.append("called")
        _ = context
        return object()

    def register() -> ProviderRegistration:
        return ProviderRegistration(
            descriptor=ProviderDescriptor(
                provider_id="bad.sdk",
                display_name="Bad",
                provider_version="1.0.0",
                sdk_compatibility=">=2",
                capabilities=(CapabilityId.LINEAGE,),
            ),
            bindings=(CapabilityBinding(capability_id=CapabilityId.LINEAGE, factory=factory),),
        )

    eps = [
        FakeEntryPoint(
            name="bad",
            value="pkg.bad:register",
            dist_name="bad-dist",
            dist_version="1",
            loader=register,
        )
    ]
    with pytest.raises(ProviderDiscoveryError) as exc_info:
        discover_providers(entry_points=eps)
    assert any(item.code == "incompatible_sdk" for item in exc_info.value.errors)
    assert calls == []


def test_callable_raising_does_not_embed_exception_str() -> None:
    secret = "password=hunter2"

    def boom() -> ProviderRegistration:
        raise ValueError(secret)

    eps = [
        FakeEntryPoint(
            name="boom",
            value="pkg.boom:register",
            dist_name="boom-dist",
            dist_version="1",
            loader=boom,
        )
    ]
    with pytest.raises(ProviderDiscoveryError) as exc_info:
        discover_providers(entry_points=eps)
    joined = " ".join(item.message for item in exc_info.value.errors)
    assert secret not in joined
    assert "ValueError" in joined
