"""Descriptor and registration conformance suites."""

from __future__ import annotations

from packaging.specifiers import SpecifierSet

from governance.conformance.cases import DescriptorCase, RegistrationCase
from governance.conformance.report import ConformanceCheckResult, ConformanceReport, check
from governance.providers.capabilities import SUPPORTED_CAPABILITIES
from governance.providers.contracts import (
    PROVIDER_SDK_API_VERSION,
    ProviderDescriptor,
    ProviderRegistration,
)
from governance.providers.registry import ProviderRegistry


def _contractual_snapshot(registration: ProviderRegistration) -> tuple[object, ...]:
    descriptor = registration.descriptor
    return (
        descriptor.provider_id,
        descriptor.display_name,
        descriptor.provider_version,
        descriptor.sdk_compatibility,
        tuple(cap.value for cap in descriptor.capabilities),
        tuple(binding.capability_id.value for binding in registration.bindings),
        registration.config_validator is not None,
    )


def run_descriptor_conformance(case: DescriptorCase) -> ConformanceReport:
    registration = case.registration
    results: list[ConformanceCheckResult] = []

    if not isinstance(registration, ProviderRegistration):
        return ConformanceReport(
            results=(
                check(
                    id="descriptor_type",
                    passed=False,
                    message="registration must be a ProviderRegistration",
                    path="/registration",
                ),
            )
        )

    descriptor = registration.descriptor
    results.append(
        check(
            id="descriptor_type",
            passed=isinstance(descriptor, ProviderDescriptor),
            message="descriptor must be a ProviderDescriptor",
            path="/descriptor",
        )
    )
    if not isinstance(descriptor, ProviderDescriptor):
        return ConformanceReport(results=tuple(results))

    results.append(
        check(
            id="provider_id_stable",
            passed=bool(descriptor.provider_id),
            message=f"provider_id is {descriptor.provider_id!r}",
            path="/descriptor/provider_id",
        )
    )
    results.append(
        check(
            id="provider_version_valid",
            passed=bool(descriptor.provider_version),
            message=f"provider_version is {descriptor.provider_version!r}",
            path="/descriptor/provider_version",
        )
    )
    results.append(
        check(
            id="sdk_compatibility_valid",
            passed=bool(descriptor.sdk_compatibility),
            message=f"sdk_compatibility is {descriptor.sdk_compatibility!r}",
            path="/descriptor/sdk_compatibility",
        )
    )

    caps = descriptor.capabilities
    unknown = [cap.value for cap in caps if cap not in SUPPORTED_CAPABILITIES]
    results.append(
        check(
            id="capabilities_known",
            passed=not unknown,
            message=(
                "all declared capabilities are known"
                if not unknown
                else f"unknown capabilities: {unknown}"
            ),
            path="/descriptor/capabilities",
        )
    )
    values = [cap.value for cap in caps]
    results.append(
        check(
            id="capabilities_unique",
            passed=len(values) == len(set(values)),
            message="declared capabilities have no duplicates",
            path="/descriptor/capabilities",
        )
    )
    results.append(
        check(
            id="capabilities_canonical_order",
            passed=values == sorted(values),
            message="declared capabilities are stored in canonical order",
            path="/descriptor/capabilities",
        )
    )

    binding_ids = [binding.capability_id.value for binding in registration.bindings]
    results.append(
        check(
            id="declared_capabilities_match_bindings",
            passed=sorted(values) == sorted(binding_ids) and len(values) == len(binding_ids),
            message=(
                "declared capabilities match bindings"
                if sorted(values) == sorted(binding_ids) and len(values) == len(binding_ids)
                else f"capabilities={values!r} bindings={binding_ids!r}"
            ),
            path="/bindings",
        )
    )

    try:
        compatible = PROVIDER_SDK_API_VERSION in SpecifierSet(descriptor.sdk_compatibility)
    except Exception as exc:  # noqa: BLE001 - surface as conformance check
        results.append(
            check(
                id="sdk_compatibility_membership",
                passed=False,
                message=f"sdk_compatibility could not be evaluated: {type(exc).__name__}",
                path="/descriptor/sdk_compatibility",
            )
        )
    else:
        results.append(
            check(
                id="sdk_compatibility_membership",
                passed=compatible,
                message=(
                    f"sdk_compatibility accepts Provider SDK API {PROVIDER_SDK_API_VERSION}"
                    if compatible
                    else (
                        f"sdk_compatibility {descriptor.sdk_compatibility!r} does not "
                        f"include Provider SDK API {PROVIDER_SDK_API_VERSION}"
                    )
                ),
                path="/descriptor/sdk_compatibility",
            )
        )

    return ConformanceReport(results=tuple(results))


def run_registration_conformance(case: RegistrationCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    register = case.register

    try:
        first = register()
    except Exception as exc:  # noqa: BLE001
        return ConformanceReport(
            results=(
                check(
                    id="registration_callable",
                    passed=False,
                    message=f"register() raised {type(exc).__name__}",
                    path="/register",
                ),
            )
        )

    results.append(
        check(
            id="registration_callable",
            passed=isinstance(first, ProviderRegistration),
            message="register() returns ProviderRegistration",
            path="/register",
        )
    )
    if not isinstance(first, ProviderRegistration):
        return ConformanceReport(results=tuple(results))

    try:
        second = register()
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="registration_deterministic",
                passed=False,
                message=f"second register() raised {type(exc).__name__}",
                path="/register",
            )
        )
        return ConformanceReport(results=tuple(results))

    if not isinstance(second, ProviderRegistration):
        results.append(
            check(
                id="registration_deterministic",
                passed=False,
                message="second register() did not return ProviderRegistration",
                path="/register",
            )
        )
        return ConformanceReport(results=tuple(results))

    snap_a = _contractual_snapshot(first)
    snap_b = _contractual_snapshot(second)
    results.append(
        check(
            id="registration_deterministic",
            passed=snap_a == snap_b,
            message=(
                "repeated register() yields equivalent contractual representation"
                if snap_a == snap_b
                else f"contractual snapshots differ: {snap_a!r} vs {snap_b!r}"
            ),
            path="/register",
        )
    )
    results.append(
        check(
            id="registration_factories_not_invoked",
            passed=True,
            message=(
                "registration harness does not invoke capability factories; "
                "operational I/O during register() is a trust-model obligation, "
                "not certified by this kit"
            ),
            path="/register",
        )
    )

    registry = ProviderRegistry()
    try:
        registry.register(first)
        results.append(
            check(
                id="registration_registry_accepts",
                passed=True,
                message="ProviderRegistry accepts registration",
                path="/registry",
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="registration_registry_accepts",
                passed=False,
                message=f"ProviderRegistry rejected registration: {type(exc).__name__}",
                path="/registry",
            )
        )

    descriptor_report = run_descriptor_conformance(DescriptorCase(registration=first))
    results.extend(descriptor_report.results)
    return ConformanceReport(results=tuple(results))


__all__ = [
    "run_descriptor_conformance",
    "run_registration_conformance",
]
