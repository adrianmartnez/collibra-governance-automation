"""Observable secret-safety conformance checks."""

from __future__ import annotations

from governance.conformance.cases import SecretSafetyCase
from governance.conformance.report import ConformanceCheckResult, ConformanceReport, check
from governance.providers.contracts import ProviderRuntimeContext


def run_secret_safety_conformance(case: SecretSafetyCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    context = case.context
    forbidden = tuple(item for item in case.forbidden_substrings if item)

    if not isinstance(context, ProviderRuntimeContext):
        return ConformanceReport(
            results=(
                check(
                    id="secret_context_type",
                    passed=False,
                    message="context must be ProviderRuntimeContext",
                    path="/context",
                ),
            )
        )

    context_repr = repr(context)
    leaked = [marker for marker in forbidden if marker and marker in context_repr]
    results.append(
        check(
            id="secret_runtime_context_repr",
            passed=not leaked,
            message=(
                "ProviderRuntimeContext repr omits forbidden secret markers"
                if not leaked
                else f"ProviderRuntimeContext repr leaked markers: {leaked!r}"
            ),
            path="/context",
        )
    )

    registration_repr = repr(case.registration)
    descriptor_repr = repr(case.registration.descriptor)
    combined = registration_repr + descriptor_repr
    leaked_reg = [marker for marker in forbidden if marker and marker in combined]
    results.append(
        check(
            id="secret_registration_repr",
            passed=not leaked_reg,
            message=(
                "registration/descriptor repr omits forbidden secret markers"
                if not leaked_reg
                else f"registration/descriptor repr leaked markers: {leaked_reg!r}"
            ),
            path="/registration",
        )
    )

    return ConformanceReport(results=tuple(results))


__all__ = ["run_secret_safety_conformance"]
