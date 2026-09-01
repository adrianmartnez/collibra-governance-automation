"""Boolean root-alignment acknowledgement for snapshot comparison."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from governance.comparison.errors import (
    CODE_ROOT_ALIGNMENT_REQUIRED,
    ComparisonError,
    DiagnosticError,
)
from governance.snapshots.models import GovernanceSnapshot


@dataclass(frozen=True, slots=True)
class RootAlignmentAck:
    """Explicit acknowledgement that differing roots may be matched."""

    align_source_roots: bool = False
    align_database_roots: bool = False


@dataclass(frozen=True, slots=True)
class RootAlignmentResult:
    """Canonical root_alignment block for the comparison result."""

    source: dict[str, str] | None
    database: dict[str, str] | None

    def to_dict(self) -> dict[str, Any]:
        return {"database": self.database, "source": self.source}


def resolve_root_alignment(
    baseline: GovernanceSnapshot,
    candidate: GovernanceSnapshot,
    ack: RootAlignmentAck,
) -> RootAlignmentResult:
    """Validate ACK flags and build material alignment metadata.

    Redundant flags when names are equal are accepted as no-ops and yield null
    entries so they do not change result identity.
    """
    errors: list[DiagnosticError] = []

    source_differs = baseline.source_name != candidate.source_name
    database_differs = baseline.database_name != candidate.database_name

    if source_differs and not ack.align_source_roots:
        errors.append(
            DiagnosticError(
                code=CODE_ROOT_ALIGNMENT_REQUIRED,
                path="/root_alignment/source",
                message="source names differ; pass --align-source-roots to acknowledge matching",
            )
        )
    if database_differs and not ack.align_database_roots:
        errors.append(
            DiagnosticError(
                code=CODE_ROOT_ALIGNMENT_REQUIRED,
                path="/root_alignment/database",
                message=(
                    "database names differ; pass --align-database-roots to acknowledge matching"
                ),
            )
        )
    if errors:
        raise ComparisonError(errors)

    source_block: dict[str, str] | None = None
    if source_differs:
        source_block = {
            "baseline": baseline.source_name,
            "candidate": candidate.source_name,
        }

    database_block: dict[str, str] | None = None
    if database_differs:
        database_block = {
            "baseline": baseline.database_name,
            "candidate": candidate.database_name,
        }

    return RootAlignmentResult(source=source_block, database=database_block)
