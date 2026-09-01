"""Public comparison package exports."""

from governance.comparison.align import (
    RootAlignmentAck,
    RootAlignmentResult,
    resolve_root_alignment,
)
from governance.comparison.errors import (
    DIAGNOSTIC_SCHEMA,
    DIAGNOSTIC_VERSION,
    ComparisonError,
    DiagnosticError,
    comparison_diagnostics_failure,
    snapshot_compare_diagnostic,
)
from governance.comparison.projection import (
    ComparableObject,
    ComparablePropertyValue,
    ComparisonObjectIdentity,
    ProjectedSnapshot,
    project_snapshot,
    validate_snapshot_shape_and_envelope,
)
from governance.comparison.result import (
    COMPARISON_SCHEMA,
    COMPARISON_VERSION,
    assert_inverse,
    build_comparison_result,
)
from governance.comparison.serialize import (
    canonical_comparison_json,
    format_comparison_human,
    write_comparison_artifact,
)

__all__ = [
    "COMPARISON_SCHEMA",
    "COMPARISON_VERSION",
    "DIAGNOSTIC_SCHEMA",
    "DIAGNOSTIC_VERSION",
    "ComparableObject",
    "ComparablePropertyValue",
    "ComparisonError",
    "ComparisonObjectIdentity",
    "DiagnosticError",
    "ProjectedSnapshot",
    "RootAlignmentAck",
    "RootAlignmentResult",
    "assert_inverse",
    "build_comparison_result",
    "canonical_comparison_json",
    "comparison_diagnostics_failure",
    "snapshot_compare_diagnostic",
    "format_comparison_human",
    "project_snapshot",
    "resolve_root_alignment",
    "validate_snapshot_shape_and_envelope",
    "write_comparison_artifact",
]
