"""ODCS ingestion errors and machine-readable diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

CODE_READ = "odcs_read_error"
CODE_PARSE = "odcs_parse_error"
CODE_UNSUPPORTED_VERSION = "odcs_unsupported_version"
CODE_SCHEMA = "odcs_schema_error"
CODE_MAPPING = "odcs_mapping_error"


@dataclass(frozen=True, slots=True)
class OdcsDiagnostic:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


class OdcsError(Exception):
    """Base class for ODCS document ingestion failures."""

    def __init__(self, errors: list[OdcsDiagnostic]) -> None:
        self.errors = tuple(sorted(errors, key=lambda e: (e.path, e.code, e.message)))
        super().__init__(self.errors[0].message if self.errors else "invalid ODCS document")


class OdcsReadError(OdcsError):
    """Unable to read an ODCS document from the filesystem."""


class OdcsParseError(OdcsError):
    """YAML/JSON parse failure or non-object root."""


class OdcsUnsupportedVersionError(OdcsError):
    """Declared apiVersion is not supported by this integration."""


class OdcsSchemaError(OdcsError):
    """Structural validation failure against the pinned ODCS schema."""


class OdcsMappingError(OdcsError):
    """Failure mapping a validated ODCS document into GovernanceGraph."""
