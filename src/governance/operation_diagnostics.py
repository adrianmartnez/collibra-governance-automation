"""Machine-readable diagnostics for true operational/integration failures."""

from __future__ import annotations

from typing import Any

OPERATION_DIAGNOSTIC_SCHEMA = "governance-operation-diagnostics"
OPERATION_DIAGNOSTIC_VERSION = "1"
CODE_OPERATIONAL = "operational_failure"


def operation_diagnostics_failure(message: str) -> dict[str, Any]:
    return {
        "diagnostic_schema": OPERATION_DIAGNOSTIC_SCHEMA,
        "diagnostic_version": OPERATION_DIAGNOSTIC_VERSION,
        "errors": [
            {
                "code": CODE_OPERATIONAL,
                "message": message,
            }
        ],
        "ok": False,
    }
