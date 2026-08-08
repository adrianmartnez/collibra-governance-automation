"""Open Data Contract Standard (ODCS) v3.1.0 ingestion into GovernanceGraph."""

from governance.integrations.odcs.errors import (
    CODE_MAPPING,
    CODE_PARSE,
    CODE_READ,
    CODE_SCHEMA,
    CODE_UNSUPPORTED_VERSION,
    OdcsDiagnostic,
    OdcsError,
    OdcsMappingError,
    OdcsParseError,
    OdcsReadError,
    OdcsSchemaError,
    OdcsUnsupportedVersionError,
)
from governance.integrations.odcs.load import load_odcs_document
from governance.integrations.odcs.schema import validate_odcs_document

__all__ = [
    "CODE_MAPPING",
    "CODE_PARSE",
    "CODE_READ",
    "CODE_SCHEMA",
    "CODE_UNSUPPORTED_VERSION",
    "OdcsDiagnostic",
    "OdcsError",
    "OdcsMappingError",
    "OdcsParseError",
    "OdcsReadError",
    "OdcsSchemaError",
    "OdcsUnsupportedVersionError",
    "load_odcs_document",
    "validate_odcs_document",
]
