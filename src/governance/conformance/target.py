"""Target capability conformance suites (partial; not full provider conformance)."""

from __future__ import annotations

from governance.conformance.cases import MutationCase, PlanningCase, PreflightCase, RemoteReadCase
from governance.conformance.report import (
    ConformanceCheckResult,
    ConformanceReport,
    check,
    merge_reports,
)
from governance.providers.capabilities import CapabilityId
from governance.providers.contracts import ProviderRegistration


def _construct(
    registration: ProviderRegistration, capability: CapabilityId, context: object
) -> object:
    binding = registration.binding_for(capability)
    return binding.factory(context)  # type: ignore[arg-type]


def _counter_zero(counter: object, *, check_id: str, path: str) -> ConformanceCheckResult:
    if counter is None:
        return check(
            id=check_id,
            passed=True,
            message="no mutation counter supplied; skipped measurable mutation assertion",
            path=path,
        )
    if not callable(counter):
        return check(
            id=check_id,
            passed=False,
            message="mutation_counter must be callable when provided",
            path=path,
        )
    value = int(counter())
    return check(
        id=check_id,
        passed=value == 0,
        message=(
            "measurable mutation counter remains 0"
            if value == 0
            else f"measurable mutation counter is {value}, expected 0"
        ),
        path=path,
    )


def run_remote_read_conformance(case: RemoteReadCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    capability = CapabilityId.REMOTE_STATE_READ
    try:
        instance = _construct(case.registration, capability, case.context)
    except Exception as exc:  # noqa: BLE001
        return ConformanceReport(
            results=(
                check(
                    id="remote_state_read_factory",
                    passed=False,
                    message=f"factory raised {type(exc).__name__}",
                    path="/capabilities/remote_state_read/factory",
                ),
            )
        )
    results.append(
        check(
            id="remote_state_read_factory",
            passed=True,
            message="factory constructed remote_state_read capability",
            path="/capabilities/remote_state_read/factory",
        )
    )
    read = getattr(instance, "read_remote_state", None)
    if not callable(read):
        results.append(
            check(
                id="remote_state_read_operation",
                passed=False,
                message="capability must implement read_remote_state(request)",
                path="/capabilities/remote_state_read/read_remote_state",
            )
        )
        return ConformanceReport(results=tuple(results))

    try:
        first = read(case.request)
        second = read(case.request)
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="remote_state_read_operation",
                passed=False,
                message=f"read_remote_state() raised {type(exc).__name__}",
                path="/capabilities/remote_state_read/read_remote_state",
            )
        )
        return ConformanceReport(results=tuple(results))

    if case.expect_type is not None:
        ok_type = isinstance(first, case.expect_type)
        results.append(
            check(
                id="remote_state_read_type",
                passed=ok_type,
                message=(
                    "read_remote_state() returned expected type"
                    if ok_type
                    else f"read_remote_state() returned {type(first).__name__}"
                ),
                path="/capabilities/remote_state_read/read_remote_state",
            )
        )
    else:
        results.append(
            check(
                id="remote_state_read_operation",
                passed=True,
                message="read_remote_state() completed",
                path="/capabilities/remote_state_read/read_remote_state",
            )
        )

    if case.project is not None:
        left = case.project(first)
        right = case.project(second)
        results.append(
            check(
                id="remote_state_read_deterministic",
                passed=left == right,
                message="repeated read_remote_state() yields equivalent projection",
                path="/capabilities/remote_state_read/read_remote_state",
            )
        )

    results.append(
        _counter_zero(
            case.mutation_counter,
            check_id="remote_state_read_read_only",
            path="/capabilities/remote_state_read",
        )
    )
    return ConformanceReport(results=tuple(results))


def run_planning_conformance(case: PlanningCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    capability = CapabilityId.TARGET_PLANNING
    try:
        instance = _construct(case.registration, capability, case.context)
    except Exception as exc:  # noqa: BLE001
        return ConformanceReport(
            results=(
                check(
                    id="target_planning_factory",
                    passed=False,
                    message=f"factory raised {type(exc).__name__}",
                    path="/capabilities/target_planning/factory",
                ),
            )
        )
    results.append(
        check(
            id="target_planning_factory",
            passed=True,
            message="factory constructed target_planning capability",
            path="/capabilities/target_planning/factory",
        )
    )
    build_plan = getattr(instance, "build_plan", None)
    if not callable(build_plan):
        results.append(
            check(
                id="target_planning_operation",
                passed=False,
                message="capability must implement build_plan(desired_state, remote_state)",
                path="/capabilities/target_planning/build_plan",
            )
        )
        return ConformanceReport(results=tuple(results))

    io_before = int(case.io_counter()) if callable(case.io_counter) else None
    try:
        first = build_plan(case.desired, case.remote)
        second = build_plan(case.desired, case.remote)
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="target_planning_operation",
                passed=False,
                message=f"build_plan() raised {type(exc).__name__}",
                path="/capabilities/target_planning/build_plan",
            )
        )
        return ConformanceReport(results=tuple(results))

    results.append(
        check(
            id="target_planning_operation",
            passed=True,
            message="build_plan() completed",
            path="/capabilities/target_planning/build_plan",
        )
    )

    if case.project is not None:
        left = case.project(first)
        right = case.project(second)
        results.append(
            check(
                id="target_planning_deterministic",
                passed=left == right,
                message="repeated build_plan() yields equivalent projection",
                path="/capabilities/target_planning/build_plan",
            )
        )
    else:
        results.append(
            check(
                id="target_planning_deterministic",
                passed=first == second,
                message="repeated build_plan() yields equal results",
                path="/capabilities/target_planning/build_plan",
            )
        )

    results.append(
        _counter_zero(
            case.mutation_counter,
            check_id="target_planning_no_mutation",
            path="/capabilities/target_planning",
        )
    )
    if case.pure_planning and callable(case.io_counter) and io_before is not None:
        io_after = int(case.io_counter())
        results.append(
            check(
                id="target_planning_pure",
                passed=io_after == io_before,
                message=(
                    "pure planning performed no measurable I/O"
                    if io_after == io_before
                    else f"I/O counter moved from {io_before} to {io_after}"
                ),
                path="/capabilities/target_planning",
            )
        )
    return ConformanceReport(results=tuple(results))


def run_preflight_conformance(case: PreflightCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    capability = CapabilityId.COMPATIBILITY_PREFLIGHT
    try:
        instance = _construct(case.registration, capability, case.context)
    except Exception as exc:  # noqa: BLE001
        return ConformanceReport(
            results=(
                check(
                    id="compatibility_preflight_factory",
                    passed=False,
                    message=f"factory raised {type(exc).__name__}",
                    path="/capabilities/compatibility_preflight/factory",
                ),
            )
        )
    results.append(
        check(
            id="compatibility_preflight_factory",
            passed=True,
            message="factory constructed compatibility_preflight capability",
            path="/capabilities/compatibility_preflight/factory",
        )
    )
    run_preflight = getattr(instance, "run_preflight", None)
    if not callable(run_preflight):
        results.append(
            check(
                id="compatibility_preflight_operation",
                passed=False,
                message="capability must implement run_preflight()",
                path="/capabilities/compatibility_preflight/run_preflight",
            )
        )
        return ConformanceReport(results=tuple(results))

    try:
        first = run_preflight()
        second = run_preflight()
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="compatibility_preflight_operation",
                passed=False,
                message=f"run_preflight() raised {type(exc).__name__}",
                path="/capabilities/compatibility_preflight/run_preflight",
            )
        )
        return ConformanceReport(results=tuple(results))

    results.append(
        check(
            id="compatibility_preflight_operation",
            passed=True,
            message="run_preflight() completed",
            path="/capabilities/compatibility_preflight/run_preflight",
        )
    )
    if case.project is not None:
        results.append(
            check(
                id="compatibility_preflight_structured",
                passed=case.project(first) == case.project(second),
                message="repeated run_preflight() yields equivalent projection",
                path="/capabilities/compatibility_preflight/run_preflight",
            )
        )
    results.append(
        _counter_zero(
            case.mutation_counter,
            check_id="compatibility_preflight_no_mutation",
            path="/capabilities/compatibility_preflight",
        )
    )
    return ConformanceReport(results=tuple(results))


def run_mutation_conformance(case: MutationCase) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    if not case.authorized:
        return ConformanceReport(
            results=(
                check(
                    id="authorized_mutation_harness",
                    passed=False,
                    message=(
                        "MutationCase.authorized must be True; conformance never "
                        "bypasses core authorization. Passing mutation conformance "
                        "does not grant permission to bypass core authorization."
                    ),
                    path="/capabilities/authorized_mutation",
                ),
            )
        )

    capability = CapabilityId.AUTHORIZED_MUTATION
    try:
        instance = _construct(case.registration, capability, case.context)
    except Exception as exc:  # noqa: BLE001
        return ConformanceReport(
            results=(
                check(
                    id="authorized_mutation_factory",
                    passed=False,
                    message=f"factory raised {type(exc).__name__}",
                    path="/capabilities/authorized_mutation/factory",
                ),
            )
        )
    results.append(
        check(
            id="authorized_mutation_factory",
            passed=True,
            message="factory constructed authorized_mutation capability",
            path="/capabilities/authorized_mutation/factory",
        )
    )
    execute = getattr(instance, "execute_authorized", None)
    if not callable(execute):
        results.append(
            check(
                id="authorized_mutation_operation",
                passed=False,
                message="capability must implement execute_authorized(request)",
                path="/capabilities/authorized_mutation/execute_authorized",
            )
        )
        return ConformanceReport(results=tuple(results))

    # Mutation is executed once only — never twice for determinism.
    try:
        result = execute(case.request)
    except Exception as exc:  # noqa: BLE001
        results.append(
            check(
                id="authorized_mutation_operation",
                passed=False,
                message=f"execute_authorized() raised {type(exc).__name__}",
                path="/capabilities/authorized_mutation/execute_authorized",
            )
        )
        return ConformanceReport(results=tuple(results))

    results.append(
        check(
            id="authorized_mutation_operation",
            passed=True,
            message=(
                "execute_authorized() completed under explicit authorized harness; "
                "passing mutation conformance != permission to bypass core authorization"
            ),
            path="/capabilities/authorized_mutation/execute_authorized",
        )
    )
    if case.project is not None:
        try:
            _ = case.project(result)
            results.append(
                check(
                    id="authorized_mutation_projection",
                    passed=True,
                    message="mutation result projection callable succeeded",
                    path="/capabilities/authorized_mutation/execute_authorized",
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                check(
                    id="authorized_mutation_projection",
                    passed=False,
                    message=f"mutation result projection raised {type(exc).__name__}",
                    path="/capabilities/authorized_mutation/execute_authorized",
                )
            )
    return ConformanceReport(results=tuple(results))


def run_target_capability_conformance(
    *cases: RemoteReadCase | PlanningCase | PreflightCase | MutationCase,
) -> ConformanceReport:
    """Run selected target capability suites.

    Partial suite != full provider conformance. Missing declared capabilities
    are not checked here; use run_provider_conformance for completeness.
    """
    reports: list[ConformanceReport] = []
    for case in cases:
        if isinstance(case, RemoteReadCase):
            reports.append(run_remote_read_conformance(case))
        elif isinstance(case, PlanningCase):
            reports.append(run_planning_conformance(case))
        elif isinstance(case, PreflightCase):
            reports.append(run_preflight_conformance(case))
        elif isinstance(case, MutationCase):
            reports.append(run_mutation_conformance(case))
        else:
            raise TypeError(f"unsupported target case: {type(case)!r}")
    if not reports:
        return ConformanceReport(results=())
    return merge_reports(*reports)


__all__ = [
    "run_mutation_conformance",
    "run_planning_conformance",
    "run_preflight_conformance",
    "run_remote_read_conformance",
    "run_target_capability_conformance",
]
