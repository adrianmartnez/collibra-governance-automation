"""Structured conformance reports and dedicated failure type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CheckStatus = Literal["passed", "failed"]


@dataclass(frozen=True, slots=True)
class ConformanceCheckResult:
    """One deterministic conformance check outcome."""

    id: str
    status: CheckStatus
    message: str
    path: str = "/"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("ConformanceCheckResult.id must be a non-empty string")
        if self.status not in {"passed", "failed"}:
            raise ValueError("ConformanceCheckResult.status must be 'passed' or 'failed'")
        if not isinstance(self.message, str):
            raise TypeError("ConformanceCheckResult.message must be a string")
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("ConformanceCheckResult.path must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    """Aggregate report for a conformance run."""

    results: tuple[ConformanceCheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(item.status == "passed" for item in self.results)

    @property
    def failures(self) -> tuple[ConformanceCheckResult, ...]:
        return tuple(item for item in self.results if item.status == "failed")


class ConformanceFailure(Exception):
    """Raised by assert_conformance when a report contains failures.

    Dedicated testing-surface error. Not a ProviderError subclass and does not
    reuse provider runtime machine diagnostic codes.
    """

    def __init__(self, report: ConformanceReport) -> None:
        if not isinstance(report, ConformanceReport):
            raise TypeError("ConformanceFailure requires a ConformanceReport")
        self.report = report
        failed = report.failures
        summary = "; ".join(f"{item.id}: {item.message}" for item in failed) or "conformance failed"
        super().__init__(summary)


def assert_conformance(report: ConformanceReport) -> None:
    """Raise ConformanceFailure when report.passed is False."""
    if not isinstance(report, ConformanceReport):
        raise TypeError("assert_conformance requires a ConformanceReport")
    if not report.passed:
        raise ConformanceFailure(report)


def merge_reports(*reports: ConformanceReport) -> ConformanceReport:
    results: list[ConformanceCheckResult] = []
    for report in reports:
        if not isinstance(report, ConformanceReport):
            raise TypeError("merge_reports requires ConformanceReport instances")
        results.extend(report.results)
    return ConformanceReport(results=tuple(results))


def check(
    *,
    id: str,
    passed: bool,
    message: str,
    path: str = "/",
) -> ConformanceCheckResult:
    return ConformanceCheckResult(
        id=id,
        status="passed" if passed else "failed",
        message=message,
        path=path,
    )


__all__ = [
    "ConformanceCheckResult",
    "ConformanceFailure",
    "ConformanceReport",
    "assert_conformance",
    "check",
    "merge_reports",
]
