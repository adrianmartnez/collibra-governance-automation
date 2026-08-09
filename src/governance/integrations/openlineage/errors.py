"""OpenLineage event ingestion errors and machine-readable diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

CODE_READ = "openlineage_read_error"
CODE_PARSE = "openlineage_parse_error"
CODE_UNSUPPORTED_SCHEMA = "openlineage_unsupported_schema"
CODE_VALIDATION = "openlineage_validation_error"
CODE_MAPPING = "openlineage_mapping_error"


@dataclass(frozen=True, slots=True)
class OpenLineageDiagnostic:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


class OpenLineageError(Exception):
    """Base class for OpenLineage event ingestion failures."""

    def __init__(self, errors: list[OpenLineageDiagnostic]) -> None:
        self.errors = tuple(sorted(errors, key=lambda e: (e.path, e.code, e.message)))
        super().__init__(self.errors[0].message if self.errors else "invalid OpenLineage document")


class OpenLineageReadError(OpenLineageError):
    """Unable to read an OpenLineage JSON file from the filesystem."""


class OpenLineageParseError(OpenLineageError):
    """JSON parse failure or invalid root shape."""


class OpenLineageUnsupportedSchemaError(OpenLineageError):
    """Declared event schemaURL is not a supported OpenLineage core 2-0-2 URL."""


class OpenLineageValidationError(OpenLineageError):
    """Structural validation failure against the consumed OpenLineage subset."""


class OpenLineageMappingError(OpenLineageError):
    """Failure mapping validated OpenLineage events into GovernanceGraph."""
