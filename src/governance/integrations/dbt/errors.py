"""dbt Manifest ingestion errors and machine-readable diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

CODE_READ = "dbt_read_error"
CODE_PARSE = "dbt_parse_error"
CODE_UNSUPPORTED_MANIFEST_VERSION = "dbt_unsupported_manifest_version"
CODE_VALIDATION = "dbt_validation_error"
CODE_MAPPING = "dbt_mapping_error"


@dataclass(frozen=True, slots=True)
class DbtDiagnostic:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


class DbtError(Exception):
    """Base class for dbt Manifest ingestion failures."""

    def __init__(self, errors: list[DbtDiagnostic]) -> None:
        self.errors = tuple(sorted(errors, key=lambda e: (e.path, e.code, e.message)))
        super().__init__(self.errors[0].message if self.errors else "invalid dbt Manifest")


class DbtReadError(DbtError):
    """Unable to read a dbt Manifest from the filesystem."""


class DbtParseError(DbtError):
    """JSON parse failure or non-object root."""


class DbtUnsupportedManifestVersionError(DbtError):
    """Declared metadata.dbt_schema_version is not supported."""


class DbtValidationError(DbtError):
    """Structural validation failure against the consumed Manifest v12 subset."""


class DbtMappingError(DbtError):
    """Failure mapping a validated dbt Manifest into GovernanceGraph."""
