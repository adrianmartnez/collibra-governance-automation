"""Reconciliation safety, overlay, assumptions, and explainability."""

from governance.reconciliation.assumptions import (
    RECONCILIATION_ASSUMPTIONS_SCHEMA,
    RECONCILIATION_ASSUMPTIONS_VERSION,
    assumptions_content_identity,
    build_reconciliation_assumptions,
    empty_assumptions,
    recompute_assumptions_on_saved_boundary,
    validate_assumptions_safety,
)
from governance.reconciliation.errors import (
    ExplainError,
    ReconciliationError,
    explain_diagnostics_failure,
    reconciliation_diagnostics_failure,
)
from governance.reconciliation.explain import (
    build_explain_result,
    canonical_explain_json,
    format_explain_human,
    load_object_identity,
    write_explain_artifact,
)
from governance.reconciliation.overlay import apply_reconciliation_overlay
from governance.reconciliation.physical_index import (
    PhysicalReconciliationIndex,
    build_physical_reconciliation_index,
    cross_check_known_objects,
)
from governance.reconciliation.safety import ReconciliationAssessment, assess_reconciliation
from governance.reconciliation.sources import (
    ReconciliationSourceBundle,
    compose_reconciliation_sources,
    has_reconciliation_source_flags,
)

__all__ = [
    "ExplainError",
    "PhysicalReconciliationIndex",
    "RECONCILIATION_ASSUMPTIONS_SCHEMA",
    "RECONCILIATION_ASSUMPTIONS_VERSION",
    "ReconciliationAssessment",
    "ReconciliationError",
    "ReconciliationSourceBundle",
    "apply_reconciliation_overlay",
    "assess_reconciliation",
    "assumptions_content_identity",
    "build_explain_result",
    "build_physical_reconciliation_index",
    "build_reconciliation_assumptions",
    "canonical_explain_json",
    "compose_reconciliation_sources",
    "cross_check_known_objects",
    "empty_assumptions",
    "explain_diagnostics_failure",
    "format_explain_human",
    "has_reconciliation_source_flags",
    "load_object_identity",
    "reconciliation_diagnostics_failure",
    "recompute_assumptions_on_saved_boundary",
    "validate_assumptions_safety",
    "write_explain_artifact",
]
